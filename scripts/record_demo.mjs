// Record only the Ripple page. Stop with recordings/STOP or Ctrl-C.
import { chromium } from 'playwright';
import { mkdir, access, writeFile, unlink } from 'node:fs/promises';
const dir = `recordings/take-${new Date().toISOString().replaceAll(':', '-')}`;
await mkdir(dir, { recursive: true });
await unlink('recordings/STOP').catch(() => {});
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: false });
const context = await browser.newContext({ viewport: { width: 1440, height: 1080 }, recordVideo: { dir, size: { width: 1440, height: 1080 } } });
const page = await context.newPage();
let stopping = false;
process.on('SIGINT', () => { stopping = true; });
process.on('SIGTERM', () => { stopping = true; });
const receipts = [];
let last = 0;
try {
  await page.goto('http://127.0.0.1:8050/?demo=1');
  await page.locator('#status').filter({ hasText: /HELD|ARRIVED|IDLE/ }).waitFor();
  await page.screenshot({ path: `${dir}/start.png` });
  console.log(`Recording panel to ${dir}; no microphone audio.`);
  const rehearsal = process.argv.includes('--rehearse');
  if (rehearsal) {
    await page.locator('#instruction').fill('Navigate to Packing A for a handoff.');
    await page.locator('#send').click();
  }
  let inspectionSent = false;
  while (!stopping && !page.isClosed()) {
    if (await access('recordings/STOP').then(() => true, () => false)) break;
    const s = await page.evaluate(async () => (await fetch('/api/state')).json());
    for (const r of s.receipts) if (r.sequence > last) { receipts.push(r); last = r.sequence; }
    if (rehearsal && !inspectionSent && s.state === 'EXECUTING') {
      inspectionSent = true;
      await page.evaluate(async () => fetch('/api/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'pause', enabled: true }) }));
      await page.locator('#instruction').fill('Packing A is being used for inspection now. Use another available handoff station.');
      await page.locator('#send').click();
      console.log('Sent inspection update while A was EXECUTING; repair dispatch paused.');
    }
    await new Promise(resolve => setTimeout(resolve, 300));
  }
} finally {
  await writeFile(`${dir}/receipts.json`, JSON.stringify(receipts, null, 2));
  await context.close();
  await browser.close();
  console.log(`Saved video and receipts in ${dir}`);
}
