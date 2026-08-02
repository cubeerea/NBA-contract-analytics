import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Static SPA (ADR-009). `base` is relative so the built bundle works from a
 * project subpath on GitHub Pages as well as from a domain root.
 *
 * The `/headshot` proxy is load-bearing, not a convenience.
 * `cdn.nba.com` serves the portraits with no `Access-Control-Allow-Origin`
 * header, and WebGL refuses to upload a cross-origin image into a texture -
 * so deck.gl's IconLayer atlas cannot pack the CDN URLs directly from a
 * browser. Proxying makes them same-origin. In production, point
 * VITE_HEADSHOT_BASE at a same-origin mirror (see web/README.md); without one
 * the chart degrades to silhouette placeholders rather than breaking.
 */
const CDN = 'https://cdn.nba.com';

export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    open: false,
    proxy: {
      '/headshot': {
        target: CDN,
        changeOrigin: true,
        rewrite: (path) =>
          path.replace(/^\/headshot\//, '/headshots/nba/latest/1040x760/')
      }
    }
  },
  preview: {
    port: 4173,
    proxy: {
      '/headshot': {
        target: CDN,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/headshot\//, '/headshots/nba/latest/1040x760/')
      }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 2500,
    rollupOptions: {
      output: {
        // deck.gl is most of the bundle and changes far less often than app
        // code, so it gets its own long-lived chunk.
        manualChunks: (id) =>
          /node_modules\/(@deck\.gl|@luma\.gl|@math\.gl|@loaders\.gl|@probe\.gl)\//.test(id)
            ? 'deckgl'
            : undefined
      }
    }
  }
});
