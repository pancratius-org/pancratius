// research-only: screenshots local HTML with the repo's playwright/chromium.
// usage: node shot.mjs <file.html> <out.png>            (single)
//        node shot.mjs --manifest <manifest.json>        (batch, reuses one browser)
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 760, height: 1200 }, deviceScaleFactor: 2 });
const shoot = async (html, png) => {
  const p = await ctx.newPage();
  await p.goto('file://' + html);
  await p.screenshot({ path: png, fullPage: true });
  await p.close();
};

if (process.argv[2] === '--manifest') {
  const items = JSON.parse(readFileSync(process.argv[3], 'utf8'));
  for (const it of items) await shoot(it.html, it.png);
  console.log('rendered', items.length, 'pages');
} else {
  await shoot(process.argv[2], process.argv[3]);
  console.log('shot ok ->', process.argv[3]);
}
await b.close();
