import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// The offline share bundle: the same app built as one JS file and one CSS file with every asset
// (fonts included) inlined, because a page opened from file:// cannot load lazy chunks. The API
// inlines both files and the run's data into a single HTML file (GET /runs/{id}/export/share).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: './',
  build: {
    outDir: 'dist-share',
    emptyOutDir: true,
    cssCodeSplit: false,
    assetsInlineLimit: 100_000_000,
    chunkSizeWarningLimit: 4000,
    modulePreload: false,
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
});
