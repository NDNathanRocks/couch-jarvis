/**
 * Calls `send` at most once per `ms` while values stream in (a dragging thumb), always with the
 * newest value, and `flush()` sends the final one at once. Keeps a drag well under the PC's limit.
 */
export function createThrottle(initial: (value: number) => void, ms = 120) {
  let send = initial;
  let last = 0;
  let pending: number | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const fire = () => {
    timer = null;
    if (pending === null) return;
    const value = pending;
    pending = null;
    last = Date.now();
    send(value);
  };
  return {
    /** Point the throttle at the newest callback (call from an effect, not while rendering). */
    setSend(next: (value: number) => void) {
      send = next;
    },
    push(value: number) {
      pending = value;
      const wait = ms - (Date.now() - last);
      if (wait <= 0) {
        if (timer) clearTimeout(timer);
        fire();
      } else if (!timer) {
        timer = setTimeout(fire, wait);
      }
    },
    flush(value?: number) {
      if (value !== undefined) pending = value;
      if (timer) clearTimeout(timer);
      fire();
    },
    cancel() {
      if (timer) clearTimeout(timer);
      timer = null;
      pending = null;
    },
  };
}
