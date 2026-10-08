import { fileURLToPath } from "url";
import { dirname, resolve } from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const rootDir = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        main: resolve(rootDir, "index.html"),
        admin: resolve(rootDir, "admin/index.html"),
      },
    },
  },
});
