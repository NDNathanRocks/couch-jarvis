/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      manifest: false, // public/manifest.json is the single source of truth
      includeAssets: ["icons/*.png", "manifest.json"],
      workbox: {
        globPatterns: ["**/*.{js,css,html,png,svg,json}"],
        navigateFallback: "/index.html",
        // never cache realtime traffic or the daemon API
        navigateFallbackDenylist: [/^\/api\//, /^\/events/, /^\/ws/],
      },
    }),
  ],
  build: { target: "es2022", sourcemap: false },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
