import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Windows bind mounts (compose dev profile) do not propagate file events; VITE_POLL=1 polls.
const polling = process.env.VITE_POLL ? { usePolling: true, interval: 300 } : undefined;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    watch: polling,
    proxy: { '/api': process.env.GUSTSIM_API || 'http://127.0.0.1:8000' },
  },
  build: {
    chunkSizeWarningLimit: 1400,
    rollupOptions: {
      output: {
        // vtk.js is large and only needed on viewer routes; keeping it in its own chunk
        // lets the dashboard load without it and caches it independently of app code.
        manualChunks(id) {
          if (id.includes('@kitware/vtk.js')) return 'vtk';
          if (id.includes('uplot')) return 'charts';
          if (/node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'react';
          return undefined;
        },
      },
    },
  },
});
