// Hours-saved estimator on the landing page. The build renders the result for the default
// assumptions (works without JavaScript); this module recalculates on every edit.
// compute() mirrors enps/export/site_data.estimate(); keep both in step.
import { fmtHours, fmtNumber, parseNumber } from "./lib/format.js";

/** Hours per year from the assumptions (shares as fractions). */
export function compute(values, machineSeconds) {
  const comments = values.responses_per_cycle * values.comment_share * values.cycles_per_year;
  const manual = comments * (values.minutes_read_classify + values.minutes_anonymize) / 60;
  const review = comments * values.review_share * values.minutes_per_review / 60;
  const machine = machineSeconds == null ? null : comments * machineSeconds / 3600;
  return { comments, manual, review, saved: manual - review, machine };
}

/** Value of one input, or null when it is not a number inside its limits. */
export function readInput(input, lang) {
  const value = parseNumber(input.value, lang);
  const min = Number(input.dataset.min);
  const max = Number(input.dataset.max);
  if (value === null || value < min || value > max) return null;
  return input.dataset.kind === "share" ? value / 100 : value;
}

export function initEstimator(page, form = document.getElementById("estimator")) {
  if (!form) return;
  const lang = page.lang;
  const machineSeconds = page.estimator ? page.estimator.machine_s : null;
  const inputs = [...form.querySelectorAll("input[data-kind]")];
  const edited = new Set();
  const out = (name) => form.querySelector(`[data-out="${name}"]`);

  const errorFor = (input) => document.getElementById(`${input.id}-error`);

  function showError(input, show) {
    const error = errorFor(input);
    if (show) {
      input.setAttribute("aria-invalid", "true");
      error.textContent = page.t("estimate.invalid", {
        min: fmtNumber(Number(input.dataset.min), lang),
        max: fmtNumber(Number(input.dataset.max), lang),
      });
      error.hidden = false;
    } else {
      input.removeAttribute("aria-invalid");
      error.textContent = "";
      error.hidden = true;
    }
  }

  function render() {
    const values = {};
    for (const input of inputs) {
      const value = readInput(input, lang);
      if (value === null) return; // keep the last valid result while a field is invalid
      values[input.name] = value;
    }
    const result = compute(values, machineSeconds);
    out("comments").textContent = fmtNumber(result.comments, lang);
    out("manual").textContent = fmtHours(result.manual, lang);
    out("review").textContent = fmtHours(result.review, lang);
    out("saved").textContent = page.t("estimate.hours", { value: fmtHours(result.saved, lang) });
    const machine = out("machine");
    if (machine && result.machine !== null) machine.textContent = fmtHours(result.machine, lang);
  }

  form.addEventListener("input", (event) => {
    const input = event.target;
    if (!inputs.includes(input)) return;
    edited.add(input);
    if (readInput(input, lang) !== null) showError(input, false);
    render();
  });

  form.addEventListener("focusout", (event) => {
    const input = event.target;
    if (inputs.includes(input) && edited.has(input)) showError(input, readInput(input, lang) === null);
  });

  form.addEventListener("submit", (event) => event.preventDefault());

  form.addEventListener("reset", () => {
    // Values are restored after the reset event, so wait one task.
    setTimeout(() => {
      edited.clear();
      inputs.forEach((input) => showError(input, false));
      render();
    }, 0);
  });
}
