// Error state of form fields, after the accessible error announcement guide:
//   * every control starts at aria-invalid="false";
//   * the error text is listed in aria-describedby only while it is shown
//     (the control names its error element with data-error);
//   * an error that appears when the user leaves or changes a field is announced once,
//     a moment later, through the page's polite live region.
// A field is an element with data-field="name" holding its controls and a .field-error.

import { announce } from "../lib/live.js";

const ANNOUNCE_DELAY_MS = 500;

function linkError(control, id, shown) {
  const ids = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter((x) => x && x !== id);
  if (shown) ids.push(id);
  if (ids.length) control.setAttribute("aria-describedby", ids.join(" "));
  else control.removeAttribute("aria-describedby");
}

/** Text as a screen reader reads it: aria-hidden parts left out. */
function spokenText(node) {
  if (!node) return "";
  const copy = node.cloneNode(true);
  copy.querySelectorAll('[aria-hidden="true"]').forEach((n) => n.remove());
  return copy.textContent.replace(/\s+/g, " ").trim();
}

export class FieldErrors {
  constructor(root) {
    this.fields = new Map();
    for (const box of root.querySelectorAll("[data-field]")) {
      const controls = [...box.querySelectorAll("[data-error]")];
      const error = box.querySelector(".field-error");
      if (!error) continue;
      this.fields.set(box.dataset.field, { box, controls, error });
      for (const control of controls) control.setAttribute("aria-invalid", "false");
    }
    this.shown = new Map();
    this.timer = 0;
    this.pending = null;
  }

  has(name) {
    return this.shown.has(name);
  }

  /** Names with a visible error, in page order. */
  names() {
    return [...this.fields.keys()].filter((name) => this.shown.has(name));
  }

  message(name) {
    return this.shown.get(name);
  }

  show(name, message, { announce: speak = false } = {}) {
    const field = this.fields.get(name);
    if (!field) return;
    const changed = this.shown.get(name) !== message;
    field.error.textContent = message;
    field.error.hidden = false;
    for (const control of field.controls) {
      control.setAttribute("aria-invalid", "true");
      linkError(control, field.error.id, true);
    }
    field.box.classList.add("is-invalid");
    this.shown.set(name, message);
    if (speak && changed) this.announceLater(name, field, message);
  }

  clear(name) {
    const field = this.fields.get(name);
    if (!field || !this.shown.has(name)) return;
    field.error.hidden = true;
    field.error.textContent = "";
    for (const control of field.controls) {
      control.setAttribute("aria-invalid", "false");
      linkError(control, field.error.id, false);
    }
    field.box.classList.remove("is-invalid");
    this.shown.delete(name);
    if (this.pending === name) {
      clearTimeout(this.timer);
      this.pending = null;
    }
  }

  clearAll() {
    for (const name of this.names()) this.clear(name);
  }

  /** Where focus goes to fix a field: the checked radio, the first control, or the box. */
  focusTarget(name) {
    const field = this.fields.get(name);
    if (!field) return null;
    const checked = field.controls.find((control) => control.checked);
    return checked || field.controls[0] || field.box.querySelector("[tabindex]") || field.box;
  }

  label(name) {
    const field = this.fields.get(name);
    if (!field) return "";
    const control = field.controls[0];
    const label = field.box.querySelector("legend") || (control && control.labels && control.labels[0])
      || field.box.querySelector(".label");
    return spokenText(label);
  }

  announceLater(name, field, message) {
    clearTimeout(this.timer);
    this.pending = name;
    // Wait for the screen reader to announce the control that receives focus next.
    this.timer = setTimeout(() => {
      this.pending = null;
      const label = this.label(name);
      announce(label ? `${label}: ${message}` : message);
    }, ANNOUNCE_DELAY_MS);
  }
}
