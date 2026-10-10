// Usage: npx -y -p playwright@1.63.0 node shot.mjs <abs.html> <out_dir> <id> [settle_ms]
// Prints a JSON list of PNG paths, in the order the critic should see them.
// Also writes <id>-media.json: the rendered desktop box of every <img>/<video>/poster (for the resolution guard).
import { createRequire } from 'module';
import { readFileSync, unlinkSync, writeFileSync } from 'fs';

const [html, outDir, id, settleArg] = process.argv.slice(2);
const settle = Number(settleArg ?? 8000);
const bin = process.env.PATH.split(':').find((p) => p.includes('_npx'));
const { chromium } = createRequire(bin + '/../')('playwright');

const SECTION = 1400;
const browser = await chromium.launch({ channel: 'chrome' });

async function capture(viewport, prefix, startY, maxSections) {
  const shots = [];
  const page = await browser.newPage({ viewport });
  const t0 = Date.now();
  await page.goto('file://' + html, { waitUntil: 'networkidle', timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(600);
  const fold = `${outDir}/${id}-${prefix}-fold.png`;
  await page.screenshot({ path: fold });
  shots.push(fold);
  if (prefix === 'desktop') {
    const media = await page.evaluate(() => [...document.querySelectorAll('img, video')].flatMap((el) => {
      const r = el.getBoundingClientRect();
      const srcs = [el.currentSrc || el.src, el.poster].filter(Boolean);
      return srcs.map((src) => ({ src, width: Math.round(r.width), height: Math.round(r.height) }));
    }));
    writeFileSync(`${outDir}/${id}-media.json`, JSON.stringify(media));
  }
  if (settle > 0) {
    await page.waitForTimeout(Math.max(0, settle - (Date.now() - t0)));
    const settled = `${outDir}/${id}-${prefix}-settled.png`;
    await page.screenshot({ path: settled });
    // ponytail: byte compare; a static page renders byte-identical PNGs, so it costs nothing extra
    if (readFileSync(settled).equals(readFileSync(fold))) unlinkSync(settled);
    else shots.push(settled);
  }
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  let n = 0;
  for (let y = startY; y < height && n < maxSections; y += SECTION) {
    n += 1;
    const path = `${outDir}/${id}-${prefix}-${String(n).padStart(2, '0')}.png`;
    const h = Math.min(SECTION, height - y);
    await page.screenshot({ path, fullPage: true, clip: { x: 0, y, width: viewport.width, height: h } });
    shots.push(path);
  }
  await page.close();
  return shots;
}

// Both viewports run at once so the settle wait is paid once, not twice.
const [desktop, mobile] = await Promise.all([
  capture({ width: 1440, height: 900 }, 'desktop', 0, Infinity),
  capture({ width: 390, height: 844 }, 'mobile', 844, 2),
]);
await browser.close();
console.log(JSON.stringify([...desktop, ...mobile]));
