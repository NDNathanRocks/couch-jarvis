/** A short buzz so a tap feels like it landed. Silently does nothing where vibration is missing. */
export function tap(ms = 12): void {
  try {
    navigator.vibrate?.(ms);
  } catch {
    /* some browsers throw when vibration is blocked */
  }
}
