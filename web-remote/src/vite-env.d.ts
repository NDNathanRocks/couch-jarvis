/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** wss:// URL of the AWS relay (the SAM stack's WebSocketUrl output). Empty = LAN-only build. */
  readonly VITE_RELAY_WS_URL?: string;
  /** Set only on the OLD site: https address that pairings are forwarded to. */
  readonly VITE_HANDOFF_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
