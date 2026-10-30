// Pure decision logic, shared by the content script and the Node tests (no DOM, no chrome.*).
"use strict";

(function (root) {
  const RANK = { none: 0, low: 1, medium: 2, high: 3, critical: 4 };

  // What the user may do for each policy action, first = default button.
  const CHOICES = {
    text: {
      block: ["masked", "cancel"],            // original is never pasted
      mask: ["masked", "original", "cancel"],
      warn: ["original", "masked", "cancel"],
    },
    file: {
      block: ["cancel"],
      mask: ["cancel", "original"],           // a file cannot be masked: default is to not upload
      warn: ["original", "cancel"],
    },
    // sending a typed message: a heads-up, the user may always proceed; critical -> edit is the default
    submit: {
      block: ["cancel", "masked", "original"],
      mask: ["masked", "original", "cancel"],
      warn: ["original", "masked", "cancel"],
    },
  };

  /** assessment from the engine -> {mode, choices}; mode "allow" means go ahead silently. */
  function decide(assessment, kind = "text") {
    const action = assessment && assessment.risk !== "none" ? assessment.action : "allow";
    if (!CHOICES[kind][action]) return { mode: "allow", choices: [] };
    return { mode: action, choices: CHOICES[kind][action] };
  }

  /** The strictest of several file results (one upload can carry many files). */
  function worst(assessments) {
    return assessments.reduce((a, b) => (RANK[b.risk] > RANK[a.risk] ? b : a), { risk: "none", action: "allow" });
  }

  /** "4111 1111 1111 1111" -> "41•••••••11": enough to recognise, not enough to leak. */
  function preview(value) {
    const v = (value || "").trim();
    if (v.length <= 4) return "•".repeat(v.length);
    return v.slice(0, 2) + "•".repeat(Math.min(v.length - 4, 8)) + v.slice(-2);
  }

  const SEND_RE = /^(send|submit)\b/i;

  /** Does this control send the message? aria-label / title / tooltip "Send…", or testid send-button. */
  function isSendControl(attrs) {
    if (/(^|-)send-button$/i.test(attrs.testid || "")) return true;
    return [attrs.aria, attrs.title, attrs.tooltip].some((v) => v && SEND_RE.test(v.trim()));
  }

  /** Which key presses send. Plain Enter sends in chat apps but is a new line in webmail. */
  function isSubmitKey(ev, host) {
    if (ev.key !== "Enter" || ev.isComposing || ev.shiftKey || ev.altKey) return false;
    if (ev.ctrlKey || ev.metaKey) return true;
    return !/(^|\.)mail\.google\.com$/.test(host);
  }

  /** Is this site switched off by the user? host match, subdomains included. */
  function siteDisabled(host, disabled) {
    return (disabled || []).some((d) => host === d || host.endsWith("." + d));
  }

  const api = { decide, worst, preview, siteDisabled, isSendControl, isSubmitKey, RANK };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SDDDecide = api;
})(typeof self !== "undefined" ? self : this);
