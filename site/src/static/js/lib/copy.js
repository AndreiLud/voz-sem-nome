// Copy buttons: <button data-copy-target="#id"> or <button data-copy="text">.
// Works for buttons added later too (one delegated listener).
import { announce } from "./live.js";

const RESET_MS = 2000;

function textToCopy(button, doc) {
  if (button.dataset.copy) return button.dataset.copy;
  const target = doc.querySelector(button.dataset.copyTarget);
  if (!target) return "";
  if (target instanceof HTMLAnchorElement) return target.href;
  if ("value" in target && typeof target.value === "string") return target.value;
  return target.textContent.trim();
}

function setLabel(button, text) {
  const label = button.querySelector("span");
  (label || button).textContent = text;
}

export function initCopyButtons(page, doc = document) {
  doc.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-target], [data-copy]");
    if (!button) return;
    const text = textToCopy(button, doc);
    if (!text) return;
    const original = button.dataset.copyLabel || page.t("copy");
    try {
      await navigator.clipboard.writeText(text);
      button.dataset.state = "copied";
      setLabel(button, page.t("copied"));
      announce(page.t("copied"));
      setTimeout(() => {
        delete button.dataset.state;
        setLabel(button, original);
      }, RESET_MS);
    } catch {
      announce(page.t("copy_failed"));
    }
  });
}
