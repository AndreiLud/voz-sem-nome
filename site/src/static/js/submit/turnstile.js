// Turnstile widget (implicit rendering into .cf-turnstile). The token lands in the hidden
// input named "turnstile" that the widget adds to the form; it is single use, so the
// widget is reset after every submit attempt.

const WAIT_STEP_MS = 200;

function widget(form) {
  return form.querySelector(".cf-turnstile");
}

export function turnstileToken(form, win = window) {
  try {
    const token = win.turnstile?.getResponse(widget(form));
    if (token) return token;
  } catch {
    // widget not rendered yet
  }
  const input = form.elements.namedItem("turnstile");
  return input && "value" in input ? input.value : "";
}

/** Token, waiting up to `timeoutMs` for the widget to finish (right after a reset). */
export async function waitForToken(form, timeoutMs, win = window) {
  const deadline = Date.now() + timeoutMs;
  let token = turnstileToken(form, win);
  while (!token && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, WAIT_STEP_MS));
    token = turnstileToken(form, win);
  }
  return token;
}

export function resetTurnstile(form, win = window) {
  try {
    win.turnstile?.reset(widget(form));
  } catch {
    // nothing to reset
  }
}
