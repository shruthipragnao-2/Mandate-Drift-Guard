import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Red-team finding RT-C4-001. Vite's default is to take port 5173 if free and SILENTLY
    // walk to 5174, 5175, ... if not. The backend's CORS allowlist (backend/app/main.py) names
    // exactly `http://localhost:5173` and `http://127.0.0.1:5173`, so on any fallback port the
    // browser blocks every API call before auth is even consulted: the UI loads and renders,
    // and then every screen shows "Failed to load cases: TypeError: Failed to fetch". Nothing
    // in either process says why. Reproduced by accident during this pass -- a stale dev server
    // held 5173, Vite moved to 5174, and the preflight from that origin returned 400 with no
    // access-control-allow-origin header.
    //
    // `strictPort` makes that impossible rather than merely unlikely: if 5173 is taken, Vite
    // now exits with a clear port-in-use error instead of starting on a port the backend will
    // refuse. Failing loudly at startup over failing mysteriously mid-demo is the same
    // fail-closed preference the backend applies everywhere else.
    //
    // Deliberately NOT fixed by widening the CORS allowlist: that would trade one silent
    // failure for a looser origin policy, and the next occupied port would reintroduce it.
    port: 5173,
    strictPort: true,
  },
})
