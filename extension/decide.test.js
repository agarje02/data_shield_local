// node --test extension/decide.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { decide, worst, preview, siteDisabled } = require("./decide.js");

test("policy action maps to choices, original is never the default on block", () => {
  assert.deepStrictEqual(decide({ risk: "none", action: "allow" }), { mode: "allow", choices: [] });
  assert.deepStrictEqual(decide({ risk: "low", action: "allow" }), { mode: "allow", choices: [] });
  assert.strictEqual(decide({ risk: "medium", action: "warn" }).choices[0], "original");
  assert.strictEqual(decide({ risk: "high", action: "mask" }).choices[0], "masked");
  for (const kind of ["text", "file", "submit"]) {
    const ch = decide({ risk: "critical", action: "block" }, kind).choices;
    assert.notStrictEqual(ch[0], "original");
    assert.strictEqual(ch[ch.length - 1], "original");    // the user may still go ahead
  }
  assert.strictEqual(decide(null).mode, "allow");
});

test("blocks can be made final in the settings", () => {
  for (const kind of ["text", "file", "submit"]) {
    const ch = decide({ risk: "critical", action: "block" }, kind, { allowOverride: false }).choices;
    assert.ok(ch.length && !ch.includes("original"));
  }
  // only blocks: a warning or mask can always be passed
  assert.ok(decide({ risk: "medium", action: "warn" }, "text", { allowOverride: false }).choices.includes("original"));
});

test("worst file wins", () => {
  const w = worst([{ risk: "medium", action: "warn" }, { risk: "critical", action: "block" }, { risk: "none", action: "allow" }]);
  assert.strictEqual(w.risk, "critical");
  assert.strictEqual(worst([]).risk, "none");
});

test("preview hides the middle", () => {
  assert.strictEqual(preview("4111 1111 1111 1111"), "41••••••••11");
  assert.strictEqual(preview("123"), "•••");
});

test("submit: the user can always proceed, critical defaults to editing", () => {
  assert.deepStrictEqual(decide({ risk: "critical", action: "block" }, "submit").choices, ["cancel", "masked", "original"]);
  assert.strictEqual(decide({ risk: "medium", action: "warn" }, "submit").choices[0], "original");
  assert.strictEqual(decide({ risk: "low", action: "allow" }, "submit").mode, "allow");
});

test("send controls and submit keys", () => {
  const { isSendControl, isSubmitKey } = require("./decide.js");
  assert.ok(isSendControl({ aria: "Send message" }));                 // Gemini, Claude
  assert.ok(isSendControl({ testid: "send-button" }));                // ChatGPT
  assert.ok(isSendControl({ aria: "Send ‪(Ctrl-Enter)‬" }));  // Gmail
  assert.ok(!isSendControl({ aria: "Send feedback about this answer".replace("Send", "Give") }));
  assert.ok(!isSendControl({ aria: "Resend code" }));
  const k = (o) => ({ key: "Enter", isComposing: false, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, ...o });
  assert.ok(isSubmitKey(k({}), "gemini.google.com"));
  assert.ok(!isSubmitKey(k({ shiftKey: true }), "chatgpt.com"));       // new line
  assert.ok(!isSubmitKey(k({ isComposing: true }), "chatgpt.com"));    // IME
  assert.ok(!isSubmitKey(k({}), "mail.google.com"));                  // Enter = new line in Gmail
  assert.ok(isSubmitKey(k({ ctrlKey: true }), "mail.google.com"));
});

test("site switch matches subdomains only", () => {
  assert.ok(siteDisabled("mail.google.com", ["google.com"]));
  assert.ok(!siteDisabled("evilgoogle.com", ["google.com"]));
});
