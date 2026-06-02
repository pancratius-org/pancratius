import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1700, height: 1400 }, deviceScaleFactor: 2 });
await p.goto('file://' + process.argv[2]);
await p.screenshot({ path: process.argv[3], fullPage: true });
await b.close(); console.log('shot', process.argv[3]);
