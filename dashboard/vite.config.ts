import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The gateway serves the built app at /dashboard/; in dev the API is proxied to a local gateway.
export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/admin": "http://localhost:8080",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    // The charting library is most of the weight and is split out of the initial load.
    chunkSizeWarningLimit: 600,
  },
});
