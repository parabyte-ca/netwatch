// Draws the app icons (PNG) from the same design as favicon.svg. Run once after changing it:
//
//   NODE_PATH=$(npm root -g) node scripts/make-icons.mjs
//
// Writes public/icon-192.png, public/icon-512.png and public/icon-maskable-512.png (the
// maskable one keeps the line inside the 80% safe zone that launchers may crop to a circle).
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const { chromium } = createRequire(import.meta.url)('playwright');
const out = (name) => fileURLToPath(new URL(`../public/${name}`, import.meta.url));
const LINE = '<path d="M4 20h5l3-9 4 14 4-18 3 13h5" fill="none" stroke="#38bdf8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>';
const svg = (size, maskable) => `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="${maskable ? 0 : 6}" fill="#0f172a"/>
  <g transform="${maskable ? 'translate(4.8 4.8) scale(0.7)' : ''}">${LINE}</g></svg>`;

const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 1 });
for (const [name, size, maskable] of [['icon-192.png', 192, false], ['icon-512.png', 512, false],
  ['icon-maskable-512.png', 512, true]]) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(`<html><body style="margin:0;background:transparent">${svg(size, maskable)}</body></html>`);
  await page.locator('svg').screenshot({ path: out(name), omitBackground: true });
  console.log('wrote', name);
}
await browser.close();
