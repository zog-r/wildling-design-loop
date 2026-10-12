// Hard-banned AI design tells, measured from computed styles in a real browser (desktop viewport).
// shot.mjs runs `probe` on every variant and writes <id>-tells.json; loop.py rejects any page with hits.
// Standalone: npx -y -p playwright@1.63.0 node tells.mjs <abs.html | url>  -> prints the JSON list, exit 1 on hits.
// A URL lets you check pages that only render with live data (e.g. http://localhost:3000/dashboard).
import { createRequire } from 'module';

export function probe() {
  const hits = [];
  const vw = innerWidth, vh = innerHeight;
  const cs = (el, pseudo) => getComputedStyle(el, pseudo);
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && cs(el).visibility !== 'hidden'; };
  const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
  const label = (el) => `<${el.tagName.toLowerCase()}> "${text(el).slice(0, 50)}"`;
  const family = (s) => s.fontFamily.split(',')[0].replace(/["']/g, '').trim().toLowerCase();
  const px = (v) => parseFloat(v) || 0;

  // 1. Accent emphasis: words inside a heading or running text set in another color or font, or italic in a heading.
  for (const block of document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,blockquote,figcaption,dd')) {
    if (!visible(block)) continue;
    const b = cs(block), heading = /^H\d$/.test(block.tagName);
    for (const el of block.querySelectorAll('*')) {
      if (el.closest('a,button,code,kbd,samp,pre,svg,label') || !text(el) || !visible(el)) continue;
      const s = cs(el), why = [];
      if (!heading && !s.display.startsWith('inline')) continue; // a block sub-line in running text is layout, not emphasis
      if (s.color !== b.color) why.push('color');
      if (family(s) !== family(b)) why.push('font');
      if (heading && s.fontStyle !== b.fontStyle) why.push('italic');
      if (why.length) hits.push({ tell: 'accent-emphasis', where: `${label(el)} in <${block.tagName.toLowerCase()}>`, why: why.join('+') });
    }
  }

  // 2. Eyebrow: a small uppercase or tracked label sitting directly above a heading.
  for (const h of document.querySelectorAll('h1,h2,h3')) {
    const prev = h.previousElementSibling;
    if (!prev || !visible(prev) || !text(prev) || text(prev).length > 40) continue;
    const p = cs(prev), tracked = p.textTransform === 'uppercase' || px(p.letterSpacing) >= 0.05 * px(p.fontSize);
    if (tracked && px(p.fontSize) <= 0.6 * px(cs(h).fontSize)) hits.push({ tell: 'eyebrow', where: `${label(prev)} above ${label(h)}` });
  }

  // 3. Generic background gradient: an opaque gradient as a large surface. Translucent scrims over imagery pass.
  for (const el of document.querySelectorAll('body, body *')) {
    const r = el.getBoundingClientRect();
    if (r.width * r.height < 0.2 * vw * vh) continue;
    for (const pseudo of [null, '::before', '::after']) {
      const bg = cs(el, pseudo).backgroundImage;
      if (bg.includes('gradient(') && !bg.includes('url(') && !/rgba\(|transparent/.test(bg)) {
        hits.push({ tell: 'background-gradient', where: `<${el.tagName.toLowerCase()}${pseudo ?? ''}> ${Math.round(r.width)}x${Math.round(r.height)}` });
      }
    }
  }

  // 4. Nested cards: a boxed container (border, shadow, or fill) inside another one. Full-width bands don't count.
  const boxed = (el) => {
    if (!/^(DIV|ARTICLE|ASIDE|LI|SECTION|FIGURE|FORM)$/.test(el.tagName) || !visible(el)) return false;
    const r = el.getBoundingClientRect(), s = cs(el);
    if (r.width >= 0.9 * vw || r.width < 160 || r.height < 100) return false;
    const border = ['Top', 'Right', 'Bottom', 'Left'].every((d) => px(s[`border${d}Width`]) > 0 && s[`border${d}Style`] !== 'none');
    const fill = !/rgba\(0, 0, 0, 0\)|transparent/.test(s.backgroundColor) && s.backgroundColor !== cs(el.parentElement).backgroundColor;
    return border || s.boxShadow !== 'none' || fill;
  };
  for (const el of document.querySelectorAll('body *')) {
    if (!boxed(el)) continue;
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      if (boxed(a)) { hits.push({ tell: 'nested-cards', where: `${label(el).slice(0, 40)} inside <${a.tagName.toLowerCase()}>` }); break; }
    }
  }

  // 5. Too many fonts: more than two families carrying visible text.
  const fams = new Set();
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walk.nextNode());) {
    const el = n.parentElement;
    if (n.textContent.trim() && el && !el.closest('code,kbd,samp,pre,svg,script,style') && visible(el)) fams.add(family(cs(el)));
  }
  if (fams.size > 2) hits.push({ tell: 'too-many-fonts', where: [...fams].join(', ') });

  return hits;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const bin = process.env.PATH.split(':').find((p) => p.includes('_npx'));
  const { chromium } = createRequire(bin + '/../')('playwright');
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const target = process.argv[2];
  await page.goto(/^(https?|file):/.test(target) ? target : 'file://' + target, { waitUntil: 'networkidle', timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(1000); // client-rendered content that lands after network idle
  const hits = await page.evaluate(probe);
  await browser.close();
  console.log(JSON.stringify(hits, null, 1));
  process.exit(hits.length ? 1 : 0);
}
