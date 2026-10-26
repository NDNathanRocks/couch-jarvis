import type { PointerAction } from "../protocol";

/** What a touchpad gesture asks the PC to do. dx/dy in screen pixels, scroll in wheel notches. */
export interface PointerEmit {
  dx?: number;
  dy?: number;
  scroll?: number;
  action?: PointerAction;
}

export interface GestureOptions {
  emit: (e: PointerEmit) => void;
  now?: () => number;
  /** Cursor speed multiplier. */
  speed?: number;
  /** Fingers dragging up scroll the page down, like a phone. */
  naturalScroll?: boolean;
  /** Pointer acceleration: 0 = none (1:1), 1 = like a laptop touchpad. */
  accel?: number;
}

const TAP_MAX_MS = 260;
const TAP_MAX_MOVE_PX = 10;
const HOLD_MS = 450;
const FLUSH_MS = 40; // batch movement so a drag is ~25 messages/s, not one per touch event
const SCROLL_PX_PER_NOTCH = 28;

interface Touch {
  x: number;
  y: number;
  startX: number;
  startY: number;
  startAt: number;
}

/**
 * One-finger drag moves the cursor, tap clicks, two-finger tap right-clicks, two-finger drag
 * scrolls, and press-and-hold then drag does a click-drag. Feed it pointer down/move/up per
 * pointer id; it calls `emit` with coalesced events. Timers are injectable for tests.
 */
export class TouchpadGesture {
  private readonly touches = new Map<number, Touch>();
  private readonly emit: (e: PointerEmit) => void;
  private readonly now: () => number;
  private readonly speed: number;
  private readonly scrollSign: number;
  private readonly accel: number;
  private lastMoveAt = 0;
  private accX = 0;
  private accY = 0;
  private accScroll = 0;
  private pendingDx = 0;
  private pendingDy = 0;
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private holdTimer: ReturnType<typeof setTimeout> | null = null;
  private dragging = false;
  private maxFingers = 0;
  private moved = false;

  constructor(opts: GestureOptions) {
    this.emit = opts.emit;
    this.now = opts.now ?? Date.now;
    this.speed = opts.speed ?? 1.6;
    this.scrollSign = opts.naturalScroll === false ? -1 : 1;
    this.accel = opts.accel ?? 0;
  }

  down(id: number, x: number, y: number): void {
    this.touches.set(id, { x, y, startX: x, startY: y, startAt: this.now() });
    this.lastMoveAt = this.now();
    this.maxFingers = Math.max(this.maxFingers, this.touches.size);
    if (this.touches.size === 1) {
      this.moved = false;
      this.holdTimer = setTimeout(() => this.beginDrag(), HOLD_MS);
    } else {
      this.cancelHold();
    }
  }

  move(id: number, x: number, y: number): void {
    const t = this.touches.get(id);
    if (!t) return;
    const dx = x - t.x;
    const dy = y - t.y;
    t.x = x;
    t.y = y;
    const wasMoved = this.moved;
    if (Math.hypot(x - t.startX, y - t.startY) > TAP_MAX_MOVE_PX) {
      this.moved = true;
      if (!this.dragging) this.cancelHold();
    }
    if (this.touches.size >= 2) {
      this.scrollBy(dy / this.touches.size); // average of the fingers
    } else if (this.moved || this.dragging) {
      // the tap dead zone swallowed the first pixels: catch the cursor up once it is a drag
      if (!wasMoved && !this.dragging) this.moveBy(x - t.startX, y - t.startY);
      else this.moveBy(dx, dy);
    }
  }

  up(id: number): void {
    const t = this.touches.get(id);
    if (!t) return;
    this.touches.delete(id);
    if (this.touches.size > 0) return; // wait for the last finger
    this.cancelHold();
    this.flush();
    if (this.dragging) {
      this.dragging = false;
      this.emit({ action: "drag_end" });
    } else if (!this.moved && this.now() - t.startAt <= TAP_MAX_MS) {
      this.emit({ action: this.maxFingers >= 2 ? "right_click" : "click" });
    }
    this.maxFingers = 0;
    this.accX = this.accY = this.accScroll = 0;
  }

  cancel(): void {
    this.cancelHold();
    this.touches.clear();
    this.flush();
    if (this.dragging) {
      this.dragging = false;
      this.emit({ action: "drag_end" });
    }
    this.maxFingers = 0;
  }

  private beginDrag(): void {
    this.holdTimer = null;
    if (this.touches.size === 1 && !this.moved) {
      this.dragging = true;
      this.emit({ action: "drag_start" });
    }
  }

  private cancelHold(): void {
    if (this.holdTimer !== null) clearTimeout(this.holdTimer);
    this.holdTimer = null;
  }

  /** Slow finger: finer than 1:1 for aiming at small buttons. Quick flick: crosses the TV. */
  private gain(dx: number, dy: number): number {
    if (this.accel <= 0) return 1;
    const at = this.now();
    const dt = Math.max(8, at - this.lastMoveAt); // one touch frame at least
    this.lastMoveAt = at;
    const v = Math.hypot(dx, dy) / dt; // px per ms
    return Math.min(1 + 2 * this.accel, Math.max(0.6, 0.6 + (v - 0.1) * 1.6 * this.accel));
  }

  private moveBy(dx: number, dy: number): void {
    // keep sub-pixel remainders so slow drags are not rounded away
    const k = this.speed * this.gain(dx, dy);
    this.accX += dx * k;
    this.accY += dy * k;
    const ix = Math.trunc(this.accX);
    const iy = Math.trunc(this.accY);
    this.accX -= ix;
    this.accY -= iy;
    this.pendingDx += ix;
    this.pendingDy += iy;
    this.scheduleFlush();
  }

  private scrollBy(dy: number): void {
    this.accScroll += dy * this.scrollSign;
    const notches = Math.trunc(this.accScroll / SCROLL_PX_PER_NOTCH);
    if (notches !== 0) {
      this.accScroll -= notches * SCROLL_PX_PER_NOTCH;
      // fingers moving up (dy < 0) = page moves up = scroll DOWN (positive) when natural
      this.emit({ scroll: -notches });
    }
  }

  private scheduleFlush(): void {
    if (this.flushTimer === null) this.flushTimer = setTimeout(() => this.flush(), FLUSH_MS);
  }

  private flush(): void {
    if (this.flushTimer !== null) clearTimeout(this.flushTimer);
    this.flushTimer = null;
    if (this.pendingDx === 0 && this.pendingDy === 0) return;
    const dx = this.pendingDx;
    const dy = this.pendingDy;
    this.pendingDx = this.pendingDy = 0;
    this.emit({ dx, dy });
  }
}
