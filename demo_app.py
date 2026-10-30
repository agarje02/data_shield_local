"""Interactive test page for the detector (Gradio).

Paste any text, see detected spans highlighted, the masked text and raw JSON.
Runs inline inside Colab (private: no public link) or locally on 127.0.0.1.

Colab (in a notebook cell, not with !python, so it renders inline):
  import sys; sys.path.insert(0, CODE_DIR)
  from demo_app import build_demo
  build_demo(f"{RUN_DIR}/best").launch(share=False, inline=True, height=900)
Local:
  python demo_app.py --model runs/deberta_v0_run1/best
"""
import argparse
import json
import time

import gradio as gr

from common import Predictor, mask_text

EXAMPLES = [
    "Hi team, please update KYC for Rahul Verma. PAN ABCPD1234E, Aadhaar 2345 6789 0125, "
    "mobile +91 98765 43210, email rahul.verma@gmail.com.",
    "# .env for prod\nDATABASE_URL=postgres://app:Winter@2024@10.0.3.17:5432/main\n"
    "OPENAI_API_KEY=sk-proj-AbCdEf1234567890abcdef1234567890ABCD_zyxwvutsrqpo9876543\nLOG_LEVEL=info",
    "name,email,phone,city\nPriya Nair,priya.nair@outlook.com,9123456780,Kochi\n"
    "Arjun Mehta,arjun.m@yahoo.co.in,+91 99887 66554,Pune",
    # hard negatives: nothing here should be flagged
    "Order ORD-2025-004512 shipped via AWB 7845123690. Build 3f9c2ab deployed to prod "
    "(v2.13.4), request_id=6f1c2d9e-8b7a-4c3d-9e2f-1a2b3c4d5e6f, port 6379.",
    "Invoice INV/24-25/0456 dated 12/03/2025: 40 units of LED panels, HSN 9405, total Rs. 48,600. "
    "UPI ref 412345678901. Call our helpline 1800-266-1234.",
    "Card ending XXXX-XXXX-XXXX-4421 was charged. Password: ********. Aadhaar: XXXX XXXX 7781.",
]


def build_demo(model_dir):
    predictor = Predictor(model_dir)

    def run(text, use_thresholds):
        if not text.strip():
            return [], "", "[]", ""
        t0 = time.time()
        spans = predictor.predict([text], use_thresholds=use_thresholds)[0]
        ms = (time.time() - t0) * 1000
        pieces, last = [], 0
        for sp in sorted(spans, key=lambda s: s["start"]):
            if sp["start"] > last:
                pieces.append((text[last:sp["start"]], None))
            pieces.append((text[sp["start"]:sp["end"]], f"{sp['label']} {sp['score']:.2f}"))
            last = sp["end"]
        if last < len(text):
            pieces.append((text[last:], None))
        stats = f"{len(spans)} span(s) · {ms:.0f} ms on {predictor.device}"
        return pieces, mask_text(text, spans), json.dumps(spans, indent=2, ensure_ascii=False), stats

    with gr.Blocks(title="DataShield Local") as demo:
        gr.Markdown(f"### DataShield Local\nModel: `{model_dir}`")
        with gr.Row():
            with gr.Column():
                inp = gr.Textbox(label="Text to scan", lines=12, placeholder="Paste an email, log, .env, chat...")
                use_thr = gr.Checkbox(value=True, label="Apply tuned per-label thresholds")
                btn = gr.Button("Scan", variant="primary")
                stats = gr.Markdown()
            with gr.Column():
                hl = gr.HighlightedText(label="Detected", combine_adjacent=False, show_legend=True)
                masked = gr.Textbox(label="Masked", lines=6)
        with gr.Accordion("Raw spans (JSON)", open=False):
            raw = gr.Code(language="json")
        gr.Examples(EXAMPLES, inputs=inp)
        outputs = [hl, masked, raw, stats]
        btn.click(run, [inp, use_thr], outputs)
        inp.submit(run, [inp, use_thr], outputs)
    return demo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--share", action="store_true", help="public link; avoid with real sensitive text")
    args = ap.parse_args()
    build_demo(args.model).launch(server_name="127.0.0.1", server_port=args.port, share=args.share)


if __name__ == "__main__":
    main()
