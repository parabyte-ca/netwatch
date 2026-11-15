// Phone layout check: every page at 390 px must fit the screen (no sideways scroll).
//
//   NODE_PATH=$(npm root -g) node scripts/check-layout.mjs http://127.0.0.1:8099 admin 'password' [/devices/27 ...]
//
// Signs in, visits each page (plus any extra paths), and fails when the page is wider than the
// viewport, naming the widest elements that stick out. Run it against the demo server or a test
// install; it only reads.
import { createRequire } from 'node:module';

// Playwright is not a dependency of the app: use an installed copy (npx playwright, or NODE_PATH).
const { chromium } = createRequire(import.meta.url)('playwright');

const [base = 'http://127.0.0.1:8099', user = 'admin', password = '', ...extra] = process.argv.slice(2);
const pages = ['/', '/health-score', '/targets', '/devices', '/exposure', '/dns', '/timeline', '/map',
  '/diagnosis', '/diagnostics', '/system', '/admin', ...extra];
const WIDTH = 390;
// Phones with a larger text size (Android: Settings > Display > Font size) scale every rem.
const SCALE = Number(process.env.TEXT_SCALE || 1);

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: WIDTH, height: 844 }, locale: 'en-CA' });
const page = await ctx.newPage();
const h = { headers: { 'X-Requested-With': 'netwatch' } };
const login = await page.request.post(`${base}/api/auth/login`, { ...h, data: { username: user, password } });
if (!login.ok()) {
  console.error(`sign-in failed (HTTP ${login.status()})`);
  process.exit(2);
}
let failed = 0;
for (const path of pages) {
  await page.goto(base + path);
  if (SCALE !== 1) await page.addStyleTag({ content: `html { font-size: ${16 * SCALE}px !important; }` });
  await page.waitForTimeout(1500);
  const r = await page.evaluate((w) => {
    const wide = [];
    for (const el of document.querySelectorAll('body *')) {
      const b = el.getBoundingClientRect();
      if (b.width > 0 && b.right > w + 1 && getComputedStyle(el).position !== 'fixed') {
        // Skip children of something that scrolls sideways on purpose (tables, charts).
        let p = el.parentElement, scrolls = false;
        while (p) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') { scrolls = true; break; } p = p.parentElement; }
        if (!scrolls) wide.push({ tag: el.tagName.toLowerCase(), label: el.getAttribute('aria-label') || (el.textContent || '').trim().slice(0, 40), right: Math.round(b.right) });
      }
    }
    wide.sort((a, b) => b.right - a.right);
    return { scroll: document.documentElement.scrollWidth, wide: wide.slice(0, 3) };
  }, WIDTH);
  // A blank page (a crash, or the sign-in screen) would pass for the wrong reason.
  const blank = (await page.locator('main').innerText().catch(() => '')).trim().length < 20;
  const ok = r.scroll <= WIDTH && !blank;
  if (!ok) failed++;
  const why = blank ? '  page is blank' : r.wide.map((x) => `${x.tag} "${x.label}" to ${x.right}px`).join('; ');
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${path.padEnd(18)} width ${r.scroll}${ok ? '' : '  widest: ' + why}`);
}
await browser.close();
process.exit(failed ? 1 : 0);
