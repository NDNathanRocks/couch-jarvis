/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** wss:// URL of the AWS relay (the SAM stack's WebSocketUrl output). Empty = LAN-only build. */
  readonly VITE_RELAY_WS_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
