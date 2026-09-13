// Record the Ripple dashboard headlessly at 1920x1080 until DIR/STOP exists.
// Frames are JPEG screenshots named by wall-clock time (DIR/frames/<epoch ms>.jpg), so a cut can select any
// moment exactly. (Playwright's built-in video dropped frames while the page was static and lost the tail of
// long takes.)
//   node product/scripts/record_dashboard.mjs DIR [interval_ms]
import { chromium } from 'playwright';
import { existsSync, mkdirSync } from 'node:fs';
const dir = process.argv[2];
const interval = Number(process.argv[3] || 500);
mkdirSync(`${dir}/frames`, { recursive: true });
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
await page.goto('http://127.0.0.1:8060/');
await page.waitForTimeout(1500);
console.log('RECORDING', Date.now());
let checked = Date.now(), frames = 0;
while (!existsSync(`${dir}/STOP`)) {
  const started = Date.now();
  await page.screenshot({ path: `${dir}/frames/${started}.jpg`, type: 'jpeg', quality: 82 }).then(() => frames++).catch(() => {});
  // Watchdog: if the page stops refreshing its state for 20 s, reload it rather than record a frozen screen.
  if (started - checked > 5000) {
    checked = started;
    const stale = await page.evaluate(() => Date.now() - (window.lastPollAt || 0)).catch(() => Infinity);
    if (stale > 20000) { console.log('RELOAD', Date.now(), 'dashboard state was', Math.round(stale / 1000), 's stale'); await page.reload().catch(() => {}); }
  }
  await page.waitForTimeout(Math.max(0, interval - (Date.now() - started)));
}
console.log('SAVED', frames, 'frames in', `${dir}/frames`);
await browser.close();
