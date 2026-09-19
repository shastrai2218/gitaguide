import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Two build-time guards, then the ordinary Vite config.
 *
 * Both exist because the failure they prevent is silent. A production build
 * with `VITE_MOCK=1` serves canned answers behind a badge most people would
 * scroll past, and a production build with no `VITE_API_BASE` falls back to the
 * relative `/api`, which on Vercel is a 404 for every turn. Neither shows up in
 * a build log, so both are made into build failures instead.
 */
function assertDeployableConfig(env, mode) {
  // Mock mode in a production build is never intentional.
  if (env.VITE_MOCK === '1' && mode === 'production') {
    throw new Error(
      'VITE_MOCK=1 in a production build. Mock mode serves canned answers with '
        + 'no backend and no model. Unset VITE_MOCK (or set it to 0) before '
        + 'deploying.',
    )
  }

  // On Vercel, the API lives on another origin. The relative default cannot work.
  if (process.env.VERCEL && !env.VITE_API_BASE) {
    throw new Error(
      'VITE_API_BASE is not set for this Vercel build. It defaults to the '
        + 'relative "/api", which does not exist on the Vercel domain, so every '
        + 'turn would 404. Set it to the API origin plus /api, e.g. '
        + 'https://13-205-163-41.sslip.io/api (see docs/DEPLOY_AWS.md step 7).',
    )
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  assertDeployableConfig(env, mode)

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        // Dev only. In production the browser talks to the API origin directly
        // and CORS is what allows it (GITAGUIDE_CORS_ORIGINS on the backend).
        '/api': { target: 'http://localhost:8000', changeOrigin: true },
      },
    },
    build: {
      outDir: 'dist',
      // Source maps are kept: this is a closed beta and a tester's console
      // error is worth more than hiding the bundle.
      sourcemap: true,
    },
  }
})
