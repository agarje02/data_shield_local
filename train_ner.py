"""Fine-tune a token-classification model (BIO NER) for sensitive-data detection.

Re-running the same command resumes from the last checkpoint in --out, so a
Colab disconnect only costs the steps since the last save.

Usage (Colab):
  python train_ner.py --config configs/train_v0.yaml --data /content/data \
      --out /content/drive/MyDrive/sdd/runs/deberta_v0_run1
Smoke test (local CPU):
  python train_ner.py --config configs/train_v0.yaml --data data/v0 --out runs/smoke \
      --model microsoft/deberta-v3-small --limit 200 --max-steps 10 --eval-steps 5
"""
import argparse
import dataclasses
import json
import math
import os
import shutil
import time
from pathlib import Path

import numpy as np
import torch
import yaml
from transformers import (AutoModelForTokenClassification, AutoTokenizer, DataCollatorForTokenClassification,
                          Trainer, TrainingArguments)
from transformers.trainer_utils import get_last_checkpoint

from common import bio_entities, build_label_maps, load_labels, read_jsonl, spans_to_token_labels

HERE = Path(__file__).resolve().parent


class WindowDataset(torch.utils.data.Dataset):
    def __init__(self, features):
        self.features = features

    def __len__(self):
        return len(self.features)

    def __getitem__(self, i):
        return self.features[i]


def tokenize_rows(rows, tokenizer, label2id, max_length, stride):
    """Each row may become several 512-token windows (overlapping by `stride`)."""
    texts = [r["text"] for r in rows]
    enc = tokenizer(texts, truncation=True, max_length=max_length, stride=stride,
                    return_overflowing_tokens=True, return_offsets_mapping=True)
    features = []
    for w, row_idx in enumerate(enc["overflow_to_sample_mapping"]):
        labels = spans_to_token_labels(enc["offset_mapping"][w], enc.sequence_ids(w),
                                       rows[row_idx]["spans"], label2id)
        feat = {"input_ids": enc["input_ids"][w], "attention_mask": enc["attention_mask"][w], "labels": labels}
        if "token_type_ids" in enc:
            feat["token_type_ids"] = enc["token_type_ids"][w]
        features.append(feat)
    return features


def make_compute_metrics(id2label, labels):
    def compute(eval_pred):
        preds, gold = eval_pred.predictions, eval_pred.label_ids
        tp, fp, fn = {l: 0 for l in labels}, {l: 0 for l in labels}, {l: 0 for l in labels}
        for p_row, g_row in zip(preds, gold):
            mask = g_row != -100
            p_tags = [id2label[int(x)] for x in p_row[mask]]
            g_tags = [id2label[int(x)] for x in g_row[mask]]
            p_ents, g_ents = set(bio_entities(p_tags)), set(bio_entities(g_tags))
            for lab, _, _ in p_ents & g_ents:
                tp[lab] += 1
            for lab, _, _ in p_ents - g_ents:
                fp[lab] += 1
            for lab, _, _ in g_ents - p_ents:
                fn[lab] += 1

        def prf(t, f_p, f_n):
            p = t / (t + f_p) if t + f_p else 0.0
            r = t / (t + f_n) if t + f_n else 0.0
            return p, r, (2 * p * r / (p + r) if p + r else 0.0)

        p, r, f = prf(sum(tp.values()), sum(fp.values()), sum(fn.values()))
        out = {"span_precision": p, "span_recall": r, "span_f1": f}
        for lab in labels:
            out[f"f1_{lab}"] = prf(tp[lab], fp[lab], fn[lab])[2]
        return out
    return compute


def fp32_kwargs():
    import transformers
    key = "dtype" if int(transformers.__version__.split(".")[0]) >= 5 else "torch_dtype"
    return {key: torch.float32}


def training_args(**kwargs):
    """TrainingArguments with only the fields this transformers version supports
    (e.g. group_by_length was removed in newer releases)."""
    supported = {f.name for f in dataclasses.fields(TrainingArguments)}
    dropped = sorted(k for k in kwargs if k not in supported)
    if dropped:
        print(f"note: this transformers version does not support {dropped}; skipping")
    return TrainingArguments(**{k: v for k, v in kwargs.items() if k in supported})


def argmax_logits(logits, labels):
    return logits.argmax(dim=-1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "configs" / "train_v0.yaml"))
    ap.add_argument("--data", required=True, help="folder with train.jsonl / val.jsonl")
    ap.add_argument("--out", required=True, help="run folder; checkpoints + best/ go here")
    ap.add_argument("--model", help="override model_name from config")
    ap.add_argument("--limit", type=int, default=0, help="use only N train rows (smoke tests)")
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--eval-steps", type=int, help="override eval/save steps")
    ap.add_argument("--epochs", type=float, help="override num_train_epochs")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    model_name = args.model or cfg["model_name"]
    labels_file = Path(cfg["labels_file"])
    if not labels_file.is_absolute():
        labels_file = HERE / labels_file
    labels = load_labels(labels_file)
    label2id, id2label = build_label_maps(labels)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = Path(args.data)

    train_rows = read_jsonl(data / "train.jsonl")
    val_rows = read_jsonl(data / "val.jsonl")
    if args.limit:
        train_rows = train_rows[:args.limit]
        val_rows = val_rows[:max(20, args.limit // 5)]

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    train_feats = tokenize_rows(train_rows, tokenizer, label2id, cfg["max_length"], cfg["stride"])
    val_feats = tokenize_rows(val_rows, tokenizer, label2id, cfg["max_length"], cfg["stride"])
    print(f"train: {len(train_rows)} rows -> {len(train_feats)} windows | "
          f"val: {len(val_rows)} rows -> {len(val_feats)} windows")

    # fp32 master weights: newer transformers loads the checkpoint's stored dtype (fp16 for
    # deberta-v3), and AMP then fails with "Attempting to unscale FP16 gradients"
    model = AutoModelForTokenClassification.from_pretrained(
        model_name, num_labels=len(label2id), id2label=id2label, label2id=label2id, **fp32_kwargs())

    cuda = torch.cuda.is_available()
    # native bf16 needs compute capability >= 8.0 (A100/L4); T4 is 7.5 and only emulates it
    bf16 = cuda and torch.cuda.get_device_capability(0)[0] >= 8
    fp16 = cuda and not bf16
    print(f"device: {torch.cuda.get_device_name(0) if cuda else 'cpu'} | bf16={bf16} fp16={fp16}")

    epochs = args.epochs or cfg["num_train_epochs"]
    bs, accum = cfg["per_device_train_batch_size"], cfg["gradient_accumulation_steps"]
    total_steps = args.max_steps if args.max_steps > 0 else math.ceil(len(train_feats) / (bs * accum)) * epochs
    eval_steps = args.eval_steps or cfg["eval_steps"]
    save_steps = args.eval_steps or cfg["save_steps"]

    targs = training_args(
        output_dir=str(out),
        learning_rate=cfg["learning_rate"],
        num_train_epochs=epochs,
        max_steps=args.max_steps,
        per_device_train_batch_size=bs,
        per_device_eval_batch_size=cfg["per_device_eval_batch_size"],
        gradient_accumulation_steps=accum,
        warmup_steps=int(total_steps * cfg["warmup_ratio"]),
        weight_decay=cfg["weight_decay"],
        eval_strategy="steps",
        eval_steps=eval_steps,
        save_strategy="steps",
        save_steps=save_steps,
        save_total_limit=cfg["save_total_limit"],
        load_best_model_at_end=True,
        metric_for_best_model="span_f1",
        greater_is_better=True,
        logging_steps=cfg["logging_steps"],
        # under Colab's `!python` every progress-bar refresh becomes a new output line
        # (thousands per run) and the notebook tab freezes; loss still logs every logging_steps
        disable_tqdm=True,
        group_by_length=True,
        bf16=bf16,
        fp16=fp16,
        seed=cfg["seed"],
        report_to="none",
        dataloader_num_workers=2 if cuda else 0,
    )
    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=WindowDataset(train_feats),
        eval_dataset=WindowDataset(val_feats),
        data_collator=DataCollatorForTokenClassification(tokenizer),
        processing_class=tokenizer,
        compute_metrics=make_compute_metrics(id2label, labels),
        preprocess_logits_for_metrics=argmax_logits,
    )

    last = get_last_checkpoint(str(out)) if os.path.isdir(out) else None
    if last:
        print(f"resuming from {last}")
    t0 = time.time()
    trainer.train(resume_from_checkpoint=last)
    train_minutes = (time.time() - t0) / 60

    metrics = trainer.evaluate()
    print(json.dumps({k: round(v, 4) for k, v in metrics.items() if isinstance(v, float)}, indent=2))

    best = out / "best"
    trainer.save_model(str(best))
    tokenizer.save_pretrained(str(best))
    shutil.copy(labels_file, best / "labels.yaml")
    thr = best / "thresholds.json"
    if not thr.exists():
        # per-label minimum span score; tune on val later, never on test
        thr.write_text(json.dumps({"default": 0.0}, indent=2))
    data_manifest = data / "manifest.json"
    manifest = {
        "model_name": model_name,
        "data_dir": str(data),
        "data_manifest": json.loads(data_manifest.read_text(encoding="utf-8")) if data_manifest.exists() else None,
        "config": cfg,
        "overrides": {k: v for k, v in vars(args).items() if k in ("limit", "max_steps", "eval_steps", "epochs")},
        "best_checkpoint": trainer.state.best_model_checkpoint,
        "val_metrics": metrics,
        "train_windows": len(train_feats),
        "train_minutes_this_session": round(train_minutes, 1),
        "device": torch.cuda.get_device_name(0) if cuda else "cpu",
        "precision": "bf16" if bf16 else "fp16" if fp16 else "fp32",
    }
    (best / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(f"saved best model to {best}")


if __name__ == "__main__":
    main()
