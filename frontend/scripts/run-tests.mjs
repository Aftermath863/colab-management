import { build } from 'esbuild';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));
const rootDir = join(__dirname, '..');
const cacheBase = join(rootDir, 'node_modules', '.cache');
mkdirSync(cacheBase, { recursive: true });
const tempDir = mkdtempSync(join(cacheBase, 'colab-tests-'));
const outFile = join(tempDir, 'i18n.test.cjs');

try {
  await build({
    entryPoints: [join(rootDir, 'tests', 'i18n.test.tsx')],
    outfile: outFile,
    bundle: true,
    platform: 'node',
    format: 'cjs',
    target: 'node20',
    jsx: 'automatic',
    external: ['react', 'react-dom', 'react-dom/server', 'lucide-react'],
    banner: {
      js: 'globalThis.self = globalThis;',
    },
    loader: {
      '.css': 'empty',
    },
  });

  const result = spawnSync(process.execPath, ['--test', outFile], {
    stdio: 'inherit',
    cwd: rootDir,
  });
  process.exitCode = result.status ?? 1;
} finally {
  rmSync(tempDir, { recursive: true, force: true });
}
