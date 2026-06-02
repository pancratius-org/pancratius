// research-only: screenshot a standalone HTML file's #shot element to PNG.
// Used by astro_preview.py to render lineation candidates with the REAL site CSS
// (tokens/global/prose) so we compare them against the docx page faithfully.
//   node html_shot.mjs <html_path> <out_png> [width_px]
import { chromium } from "@playwright/test";

const [, , htmlPath, outPath, widthStr] = process.argv;
const width = parseInt(widthStr || "700", 10);

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ deviceScaleFactor: 2, viewport: { width, height: 800 } });
  await page.goto("file://" + htmlPath, { waitUntil: "networkidle" });
  const el = await page.$("#shot");
  await el.screenshot({ path: outPath });
} finally {
  await browser.close();
}
