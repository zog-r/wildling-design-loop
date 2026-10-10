// Usage: npx -y -p playwright@1.63.0 node rec.mjs <abs scene.html> <out_dir> <seconds> <fps> [bg_hex]
// Renders a 3D/shader scene frame by frame on a virtual clock (rAF, performance.now, Date.now and
// window.__t are all driven by us), so every frame is deterministic regardless of machine speed.
import { createRequire } from 'module';

const [html, outDir, seconds, fps, bg] = process.argv.slice(2);
const bin = process.env.PATH.split(':').find((p) => p.includes('_npx'));
const { chromium } = createRequire(bin + '/../')('playwright');

const browser = await chromium.launch({ channel: 'chrome', args: ['--use-angle=metal', '--enable-gpu'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
await page.addInitScript(() => {
  let now = 0;
  let queue = [];
  const t0 = Date.now();
  window.__t = 0;
  window.requestAnimationFrame = (cb) => queue.push(cb);
  window.cancelAnimationFrame = () => {};
  performance.now = () => now;
  Date.now = () => t0 + now;
  window.__step = (ms) => {
    now += ms;
    window.__t = now / 1000;
    const q = queue;
    queue = [];
    q.forEach((cb) => cb(now));
  };
});
await page.goto('file://' + html, { waitUntil: 'networkidle', timeout: 60000 });
if (bg) await page.addStyleTag({ content: `html,body{background:#${bg} !important}` });
await page.evaluate(() => window.__step(0));

const n = Math.round(Number(seconds) * Number(fps));
const dt = 1000 / Number(fps);
for (let i = 0; i < n; i++) {
  await page.evaluate((ms) => window.__step(ms), dt);
  await page.screenshot({ path: `${outDir}/${String(i).padStart(5, '0')}.png` });
}
await browser.close();
console.log(JSON.stringify({ frames: n }));
