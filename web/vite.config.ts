import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';
import { execSync } from 'node:child_process';

function gitSha() {
  try { return execSync('git rev-parse --short HEAD').toString().trim(); } catch { return 'dev'; }
}

export default defineConfig({
  plugins: [preact()],
  define: {
    // Shown in "Altro", so a stale home-screen app is recognisable at a glance.
    __BUILD__: JSON.stringify({ sha: gitSha(), time: new Date().toISOString() }),
  },
  server: {
    // `npm run dev` against a hub running locally on the default port.
    proxy: { '/api': 'http://127.0.0.1:5000' },
  },
});
