import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { sourcemap: false, target: "es2022" },
  test: { environment: "jsdom", globals: false },
});
