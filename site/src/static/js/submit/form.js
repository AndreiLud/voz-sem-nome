// The demo form: live character counter, validation per the forms guide (errors after
// interaction, a summary with links on submit, focus moved to it), then the POST.
// Server answers that are not field errors are announced from the role="alert" slot.

import { apiFetch } from "../lib/api.js";
import { h, icon, slot } from "../lib/dom.js";
import { FieldErrors } from "./fields.js";
import { FIELDS, apiErrorMessage, charCount, fieldErrorsFromApi, validate } from "./rules.js";
import { resetTurnstile, turnstileToken, waitForToken } from "./turnstile.js";

const TOKEN_WAIT_MS = 8000;
const POST_TIMEOUT_MS = 20000;
const TEXT_FIELDS = new Set(["comment"]);

export function initForm(page, { onAccepted }) {
  const form = document.getElementById("submit-form");
  if (!form) return;
  const t = page.t;
  const maxChars = Number(page.vars.max) || 1000;
  const errors = new FieldErrors(form);
  const ui = {
    summary: slot("error-summary"),
    list: slot("error-list"),
    feedback: slot("submit-feedback"),
    button: slot("submit-button"),
    label: slot("submit-label"),
    counter: slot("counter"),
  };
  const edited = new Set();
  let busy = false;

  const values = () => ({
    score: form.elements.score.value,
    area: form.elements.area.value,
    tenure: form.elements.tenure.value,
    comment: form.elements.comment.value,
    consent: form.elements.consent.checked,
    turnstile: turnstileToken(form),
  });

  const message = (key) => t(`submit.errors.${key}`);

  /** Re-check one field; new errors appear only when `reveal` is true (blur, change, submit). */
  function check(name, { reveal, speak = false }) {
    const key = validate(values(), { maxChars, only: [name] })[name];
    if (!key) errors.clear(name);
    else if (reveal || errors.has(name)) errors.show(name, message(key), { announce: speak });
    refreshSummary();
  }

  function updateCounter() {
    const count = charCount(form.elements.comment.value);
    const over = count - maxChars;
    ui.counter.textContent = over > 0
      ? t("submit.counter_over", { count: over })
      : t("submit.counter", { count });
    ui.counter.classList.toggle("is-over", over > 0);
  }

  // ---------------------------------------------------------------- summary

  function showSummary() {
    const names = errors.names();
    ui.list.replaceChildren(...names.map((name) => {
      const target = errors.focusTarget(name);
      const link = h("a", { href: `#${target.id}`, dataset: { field: name } }, errors.message(name));
      return h("li", {}, link);
    }));
    ui.summary.hidden = names.length === 0;
  }

  function refreshSummary() {
    if (!ui.summary.hidden) showSummary();
  }

  ui.summary.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-field]");
    if (!link) return;
    event.preventDefault();
    errors.focusTarget(link.dataset.field)?.focus();
  });

  function showErrors(found) {
    for (const name of FIELDS) {
      if (found[name]) errors.show(name, message(found[name]));
      else errors.clear(name);
    }
    showSummary();
    ui.summary.focus();
  }

  // ---------------------------------------------------------------- server feedback

  function setFeedback(text) {
    ui.feedback.replaceChildren(text
      ? h("div", { class: "alert alert--error" }, icon("error"), h("p", {}, text))
      : "");
  }

  function setBusy(on) {
    busy = on;
    if (on) ui.button.setAttribute("aria-disabled", "true");
    else ui.button.removeAttribute("aria-disabled");
    ui.label.textContent = t(on ? "submit.sending" : "submit.submit");
  }

  function handleFailure(result) {
    if (result.status === 422) {
      const found = fieldErrorsFromApi(result.data && result.data.fields);
      if (Object.keys(found).length) {
        showErrors(found);
        return;
      }
    }
    const { key, values: fill } = apiErrorMessage(result);
    setFeedback(t(`submit.api_errors.${key}`, fill || {}));
  }

  function accepted(data) {
    form.reset();
    errors.clearAll();
    edited.clear();
    updateCounter();
    ui.summary.hidden = true;
    setFeedback("");
    onAccepted(data);
  }

  // ---------------------------------------------------------------- events

  form.addEventListener("input", (event) => {
    const name = event.target.name;
    if (name === "comment") updateCounter();
    if (!FIELDS.includes(name)) return;
    edited.add(name);
    check(name, { reveal: false }); // typing only clears errors, it never adds one
  });

  form.addEventListener("change", (event) => {
    const name = event.target.name;
    if (!FIELDS.includes(name) || TEXT_FIELDS.has(name)) return;
    edited.add(name);
    check(name, { reveal: true, speak: true });
  });

  form.addEventListener("focusout", (event) => {
    const name = event.target.name;
    if (TEXT_FIELDS.has(name) && edited.has(name)) check(name, { reveal: true, speak: true });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    setFeedback("");
    const others = validate(values(), { maxChars, only: FIELDS.filter((f) => f !== "turnstile") });
    setBusy(true);
    // Right after a reset the widget needs a moment to issue a new token.
    const token = Object.keys(others).length ? turnstileToken(form) : await waitForToken(form, TOKEN_WAIT_MS);
    const current = { ...values(), turnstile: token };
    const found = validate(current, { maxChars });
    if (Object.keys(found).length) {
      setBusy(false);
      showErrors(found);
      return;
    }
    errors.clearAll();
    ui.summary.hidden = true;
    const result = await apiFetch("/submissions", {
      method: "POST",
      timeoutMs: POST_TIMEOUT_MS,
      body: {
        score: Number(current.score),
        area: current.area,
        tenure: current.tenure,
        comment: current.comment,
        consent: true,
        lang: page.lang,
        turnstile: current.turnstile,
      },
    });
    resetTurnstile(form);
    setBusy(false);
    if (result.status === 202 && result.data && result.data.code) accepted(result.data);
    else handleFailure(result);
  });

  updateCounter();
}
