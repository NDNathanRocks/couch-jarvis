/** Compare dotted versions ("0.3.0" < "0.10.1"). Unknown or empty counts as older than anything. */
export function versionAtLeast(have: string, need: string): boolean {
  if (!/^\d+(\.\d+){0,2}/.test(have)) return false;
  const part = (v: string, i: number): number => Number.parseInt(v.split(".")[i] ?? "0", 10) || 0;
  for (let i = 0; i < 3; i++) {
    const a = part(have, i);
    const b = part(need, i);
    if (a !== b) return a > b;
  }
  return true;
}

/** The first PC app version that understands the volume and seek sliders. */
export const SLIDERS_SINCE = "0.3.0";
