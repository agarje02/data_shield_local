"""Hugging Face Space: try the DataShield Local model in the browser.

Demo only. The product runs on your own device (`pip install datashield-local`); this Space
exists so people can judge the model's quality with the sample texts. Inputs are not logged or
stored (no flagging, analytics off).
"""
import os

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr  # noqa: E402

from datashield_local.engine.core import Engine  # noqa: E402
from datashield_local.engine.model_hub import download  # noqa: E402
from datashield_local.policy import DEFAULT_POLICY  # noqa: E402

MODEL_REPO = os.environ.get("DATASHIELD_MODEL_REPO", "REPLACE/datashield-local-v2.3")
engine = Engine(download(MODEL_REPO, dest="model"), DEFAULT_POLICY)      # no audit log here

SAMPLES = [
    "Hi team, please update KYC for Rahul Verma. PAN ABCPV1234K, Aadhaar 2345 6789 0125, DOB 12/03/1990.",
    "Card 4111 1111 1111 1111 exp 09/27 cvv 123. Pay to rahul.v@okhdfcbank, OTP is 482913.",
    "DATABASE_URL=postgres://app:S3cret!2024@db.internal:5432/orders\nAWS_ACCESS_KEY_ID=AKIAIOSFODNN7ABCDEFG",
    "login rahul.verma92 / Mumbai@1990 for the vendor portal, mobile +91 98765 43210",
    "Order ORD-2025-004512 shipped via AWB 7845123690. Build 3f9c2ab deployed, port 8080, best before 03/26.",
]


def scan(text):
    if not text or not text.strip():
        return [], "", ""
    r = engine.scan(text[:20000], source="space", audit=False)
    a = r["assessment"]
    marks = [(text[f["start"]:f["end"]], f"{f['label']} · {f['severity']}") for f in a["findings"]]
    highlighted, last = [], 0
    for f in sorted(a["findings"], key=lambda f: f["start"]):
        highlighted += [(text[last:f["start"]], None), (text[f["start"]:f["end"]], f["label"])]
        last = f["end"]
    highlighted.append((text[last:], None))
    combos = "".join(f"\n- **{c['name'].replace('_', ' ')}**: {c['reason']}" for c in a["combos"])
    risk = f"**Risk: {a['risk']}** · action **{a['action']}** · {len(marks)} finding(s) · {r['ms']:.0f} ms{combos}"
    return highlighted, risk, r["masked"]


with gr.Blocks(title="DataShield Local") as demo:
    gr.Markdown("# DataShield Local\nFinds personal data, Indian IDs, payment data and secrets. "
                "**Demo only: use the samples or made-up data, not real secrets.** The real product runs "
                "on your own device.")
    inp = gr.Textbox(lines=6, label="Text", placeholder="Paste text or pick a sample below")
    gr.Examples(SAMPLES, inp)
    btn = gr.Button("Scan", variant="primary")
    out = gr.HighlightedText(label="Findings", combine_adjacent=False)
    risk = gr.Markdown()
    masked = gr.Textbox(label="Masked", lines=4)
    btn.click(scan, inp, [out, risk, masked])
    inp.submit(scan, inp, [out, risk, masked])

if __name__ == "__main__":
    demo.launch()
