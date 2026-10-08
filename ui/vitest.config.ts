import { defineConfig } from 'vitest/config';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
    resolve: {
        // Same alias as vite.config.ts, so tests can import components/ui/* and the rest directly.
        alias: {
            '@': path.resolve(__dirname, './src'),
        },
    },
    test: {
        environment: 'jsdom',
        // e2e/ contains playwright specs with their own runner (pnpm run e2e).
        exclude: ['e2e/**', 'node_modules/**'],
    },
});
