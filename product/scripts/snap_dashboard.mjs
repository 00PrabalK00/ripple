// Screenshot the Ripple dashboard: node product/scripts/snap_dashboard.mjs OUT.png [delay_ms]
import { chromium } from 'playwright';
const [out = '/tmp/ripple-dashboard.png', delay = '0'] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
await page.goto('http://127.0.0.1:8060/');
await page.waitForTimeout(3000 + Number(delay));
await page.screenshot({ path: out });
console.log(JSON.stringify({ out, errors }));
await browser.close();
