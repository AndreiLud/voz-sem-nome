// Repeats a request while the page is visible. When the tab is hidden it stops; when the
// tab comes back it asks right away and goes on. It ends for good when `fetchOnce`
// answers "stop". Failures ("retry") back off up to `maxDelayMs`.
// Requests never overlap: the next one is scheduled only after the previous answer.

export class Poller {
  constructor({ fetchOnce, intervalMs, maxDelayMs = 60000, onPause = () => {}, onRetry = () => {}, doc = document }) {
    this.fetchOnce = fetchOnce;
    this.intervalMs = intervalMs;
    this.maxDelayMs = maxDelayMs;
    this.onPause = onPause;
    this.onRetry = onRetry;
    this.doc = doc;
    this.timer = 0;
    this.failures = 0;
    this.inFlight = false;
    this.stopped = true;
    this.controller = null;
    this.onVisibility = () => this.visibilityChanged();
  }

  start({ immediate = true } = {}) {
    this.stopped = false;
    this.failures = 0;
    this.doc.addEventListener("visibilitychange", this.onVisibility);
    if (this.hidden()) {
      this.onPause(true);
      return;
    }
    if (immediate) this.run();
    else this.schedule(this.intervalMs);
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    this.timer = 0;
    this.controller?.abort();
    this.doc.removeEventListener("visibilitychange", this.onVisibility);
  }

  hidden() {
    return this.doc.visibilityState === "hidden";
  }

  visibilityChanged() {
    if (this.stopped) return;
    if (this.hidden()) {
      clearTimeout(this.timer);
      this.timer = 0;
      this.onPause(true);
      return;
    }
    this.onPause(false);
    if (!this.inFlight) this.run();
  }

  schedule(delay) {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.run(), delay);
  }

  async run() {
    clearTimeout(this.timer);
    this.timer = 0;
    if (this.stopped || this.hidden()) return;
    this.inFlight = true;
    this.controller = new AbortController();
    let outcome;
    try {
      outcome = await this.fetchOnce(this.controller.signal);
    } catch {
      outcome = "retry";
    } finally {
      this.inFlight = false;
    }
    if (this.stopped) return;
    if (outcome === "stop") {
      this.stop();
      return;
    }
    let delay = this.intervalMs;
    if (outcome === "retry") {
      this.failures += 1;
      delay = Math.min(this.maxDelayMs, this.intervalMs * 2 ** this.failures);
      this.onRetry(delay);
    } else {
      this.failures = 0;
    }
    if (!this.hidden()) this.schedule(delay);
  }
}
