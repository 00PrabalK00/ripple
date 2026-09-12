// Record the Ripple dashboard headlessly at 1920x1080 until DIR/STOP exists.
//   node product/scripts/record_dashboard.mjs DIR
import { chromium } from 'playwright';
import { existsSync, mkdirSync, renameSync } from 'node:fs';
const dir = process.argv[2];
mkdirSync(dir, { recursive: true });
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 },
  recordVideo: { dir, size: { width: 1920, height: 1080 } } });
const page = await context.newPage();
await page.goto('http://127.0.0.1:8060/');
console.log('RECORDING', Date.now());
while (!existsSync(`${dir}/STOP`)) await page.waitForTimeout(250);
const video = page.video();
await context.close();
renameSync(await video.path(), `${dir}/dashboard.webm`);
console.log('SAVED', `${dir}/dashboard.webm`);
await browser.close();
