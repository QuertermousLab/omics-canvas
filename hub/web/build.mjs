// Builds the hub pages into ../static: bundles (esbuild), stylesheets and HTML.
//   cd hub/web && npm install && npm run build
import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { copyFileSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';

const out = new URL('../static/', import.meta.url).pathname;
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

await build({
  entryPoints: ['src/hub-page.ts', 'src/viewer-page.ts', 'src/crispr-page.ts'],
  outdir: out, bundle: true, splitting: true, format: 'esm', target: 'es2022', minify: true, sourcemap: true,
  chunkNames: 'chunk-[hash]', legalComments: 'linked', logLevel: 'warning',
});

const read = (f) => readFileSync(new URL(f, import.meta.url), 'utf8');
const css = { hub: ['theme', 'nav', 'hub'], viewer: ['theme', 'nav', 'manager', 'viewer'], crispr: ['theme', 'nav', 'manager', 'crispr'] };
for (const [name, parts] of Object.entries(css)) writeFileSync(`${out}${name}.css`, parts.map((p) => read(`src/${p}.css`)).join('\n'));

const hash = createHash('sha1');
for (const f of readdirSync(out).sort()) hash.update(readFileSync(out + f));
const version = hash.digest('hex').slice(0, 10);
const head = read('pages/head.html'), nav = read('pages/nav.html');
for (const page of ['hub', 'viewer', 'crispr']) {
  const html = read(`pages/${page}.html`)
    .replace('<!--HEAD-->', head).replace('<!--NAV-->', nav)
    .replace(/<!--BODY:([\w.]+)-->/, (_, f) => read(`pages/${f}`))
    .replaceAll('__BUILD__', version);
  writeFileSync(`${out}${page}.html`, html);
}
copyFileSync(new URL('pages/favicon.svg', import.meta.url), `${out}favicon.svg`);
// Genome Canvas's fonts (SIL OFL), so every page uses the same type
const fonts = new URL('../../genome-canvas/vendor/fonts/', import.meta.url).pathname;
mkdirSync(`${out}fonts`);
for (const f of readdirSync(fonts)) copyFileSync(fonts + f, `${out}fonts/${f}`);
console.log(`built ${version}: ${readdirSync(out).join(' ')}`);
