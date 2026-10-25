import type { ReactNode } from "react";

/** Line icons for the remote, drawn to match the design (one stroke weight, no emoji). */
const PATHS: Record<string, ReactNode> = {
  mic: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </>
  ),
  send: <path d="M5 12h14M13 6l6 6-6 6" />,
  play: <polygon points="7,5 19,12 7,19" fill="currentColor" />,
  pause: (
    <>
      <rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" />
      <rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" />
    </>
  ),
  prev: (
    <>
      <polygon points="19,5 9,12 19,19" fill="currentColor" />
      <line x1="6" y1="5" x2="6" y2="19" />
    </>
  ),
  next: (
    <>
      <polygon points="5,5 15,12 5,19" fill="currentColor" />
      <line x1="18" y1="5" x2="18" y2="19" />
    </>
  ),
  back10: (
    <>
      <path d="M4 12a8 8 0 1 0 2.6-5.9M4 4v5h5" />
      <text x="12" y="15.5" textAnchor="middle" fontSize="7" fontWeight="700" fill="currentColor" stroke="none">
        10
      </text>
    </>
  ),
  fwd10: (
    <>
      <path d="M20 12a8 8 0 1 1-2.6-5.9M20 4v5h-5" />
      <text x="12" y="15.5" textAnchor="middle" fontSize="7" fontWeight="700" fill="currentColor" stroke="none">
        10
      </text>
    </>
  ),
  fullscreen: <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />,
  cc: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="3" />
      <text x="12" y="15" textAnchor="middle" fontSize="8" fontWeight="700" fill="currentColor" stroke="none">
        CC
      </text>
    </>
  ),
  volDown: (
    <>
      <path d="M4 10v4h4l5 4V6L8 10H4z" />
      <path d="M17 12h4" />
    </>
  ),
  volUp: (
    <>
      <path d="M4 10v4h4l5 4V6L8 10H4z" />
      <path d="M17 12h4M19 10v4" />
    </>
  ),
  mute: (
    <>
      <path d="M4 10v4h4l5 4V6L8 10H4z" />
      <path d="M17 9l4 6M21 9l-4 6" />
    </>
  ),
  skipAd: (
    <>
      <polygon points="5,5 15,12 5,19" fill="currentColor" />
      <line x1="18" y1="5" x2="18" y2="19" />
    </>
  ),
  close: <path d="M6 6l12 12M18 6L6 18" />,
  moon: <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />,
  back: <path d="M15 6l-6 6 6 6" />,
  forward: <path d="M9 6l6 6-6 6" />,
  reload: <path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5" />,
  plus: <path d="M12 5v14M5 12h14" />,
  tabLeft: <path d="M10 6l-6 6 6 6M20 6v12" />,
  tabRight: <path d="M14 6l6 6-6 6M4 6v12" />,
  up: <path d="M6 15l6-6 6 6" />,
  down: <path d="M6 9l6 6 6-6" />,
  gear: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </>
  ),
  speakerMute: (
    <>
      <path d="M4 10v4h4l5 4V6L8 10H4z" />
      <path d="M16.5 9.5l4 5M20.5 9.5l-4 5" />
    </>
  ),
  speakerLow: (
    <>
      <path d="M4 10v4h4l5 4V6L8 10H4z" />
      <path d="M16.5 9.5a3.5 3.5 0 0 1 0 5" />
    </>
  ),
  speakerHigh: (
    <>
      <path d="M4 10v4h4l5 4V6L8 10H4z" />
      <path d="M16.5 9.5a3.5 3.5 0 0 1 0 5M19 7a7 7 0 0 1 0 10" />
    </>
  ),
  pencil: <path d="M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4" />,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  keyboard: (
    <>
      <rect x="3" y="6" width="18" height="12" rx="3" />
      <path d="M7 10h.01M11 10h.01M15 10h.01M18 10h.01M8 14h8" />
    </>
  ),
  trash: <path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13" />,
  refresh: <path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5" />,
  download: <path d="M12 4v11M7 11l5 5 5-5M5 20h14" />,
  stop: <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />,
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 22 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
