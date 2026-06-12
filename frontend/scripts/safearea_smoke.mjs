/**
 * Verifies the --safe-top seam: loads the app (Login screen, pre-auth),
 * simulates Capacitor SystemBars' native inset injection by setting
 * --safe-area-inset-* on documentElement, and asserts top padding tracks the
 * injected value dynamically (the Android edge-to-edge path). The env() leg
 * of the max() is exercised implicitly (0px on desktop Chrome).
 */

import puppeteer from 'puppeteer-core';

const URL = process.env.APP_URL || 'http://localhost:5199/';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const results = [];
const check = (name, ok, detail = '') => {
  results.push(ok);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
};

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--no-sandbox', '--window-size=390,844'],
});
const page = await browser.newPage();
await page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true });
await page.goto(URL, { waitUntil: 'networkidle2', timeout: 30000 });
await new Promise((r) => setTimeout(r, 1500));

const loginPad = () =>
  page.evaluate(() => {
    const el = document.querySelector('.dawn-bloom');
    return el ? getComputedStyle(el).paddingTop : 'no-login-root';
  });

check('login screen rendered', (await loginPad()) !== 'no-login-root');
check('baseline top padding is 24px (env()=0 on desktop)', (await loginPad()) === '24px', await loginPad());

await page.evaluate(() => {
  document.documentElement.style.setProperty('--safe-area-inset-top', '47px');
  document.documentElement.style.setProperty('--safe-area-inset-bottom', '24px');
});
await new Promise((r) => setTimeout(r, 100));

check('padding tracks injected 47px inset (24+47=71px)', (await loginPad()) === '71px', await loginPad());

await page.evaluate(() => {
  document.documentElement.style.setProperty('--safe-area-inset-top', '32px');
});
await new Promise((r) => setTimeout(r, 100));
check('padding re-tracks rotation-style change to 32px (56px)', (await loginPad()) === '56px', await loginPad());

const seam = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--safe-top').trim());
check('--safe-top seam resolves', seam.includes('47') || seam.includes('max') || seam.includes('32'), seam);

await browser.close();
const failed = results.filter((r) => !r).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
