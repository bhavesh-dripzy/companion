/**
 * Headless-Chrome smoke test for the swipe deck. Loads the dev-only
 * playground, then per deck: drags right (shortlist), drags left (pass),
 * taps to flip, clicks the action buttons, and asserts the SAVED counter,
 * prompt log, and grid toggle all respond. Screenshots land in /tmp.
 */

import puppeteer from 'puppeteer-core';

const URL = process.env.PLAYGROUND_URL || 'http://localhost:5199/deck-playground.html';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
};

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1280,900'],
});

const page = await browser.newPage();
await page.setViewport({ width: 1280, height: 900 });

const consoleErrors = [];
page.on('console', (msg) => {
  if (msg.type() === 'error') consoleErrors.push(msg.text());
});
page.on('pageerror', (err) => consoleErrors.push(String(err)));

await page.goto(URL, { waitUntil: 'networkidle2', timeout: 30000 });
await sleep(1200);

const deckCount = await page.$$eval('[data-fixture]', (sections) =>
  sections.filter((s) => s.textContent.includes('SHORTLIST')).length,
);
check('decks rendered for multi-result kinds', deckCount >= 14, `${deckCount} decks`);

const trackingClassic = await page.$$eval('[data-fixture="tracking_card"]', (els) =>
  els.length === 1 && !els[0].textContent.includes('SHORTLIST'),
);
check('single-snapshot kind keeps classic card', trackingClassic);

const deckBox = async (kind) => {
  const el = await page.$(`[data-fixture="${kind}"]`);
  await el.scrollIntoViewIfNeeded?.();
  await page.evaluate((k) => {
    document.querySelector(`[data-fixture="${k}"]`).scrollIntoView({ block: 'center' });
  }, kind);
  await sleep(400);
  return el.boundingBox();
};

const cardCenter = async (kind) => {
  const box = await deckBox(kind);
  return { x: box.x + box.width / 2, y: box.y + box.height * 0.45 };
};

const sectionText = (kind) =>
  page.$eval(`[data-fixture="${kind}"]`, (el) => el.textContent);

const drag = async (from, dx) => {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  for (let i = 1; i <= 10; i++) {
    await page.mouse.move(from.x + (dx * i) / 10, from.y + 6 * Math.sin(i / 3), { steps: 1 });
    await sleep(16);
  }
  await page.mouse.up();
  await sleep(800);
};

// --- restaurant deck: drag right = shortlist ---
let center = await cardCenter('restaurant_cards');
let before = await sectionText('restaurant_cards');
check('restaurant deck starts at 1 OF 3', before.includes('1 OF 3'), '');
check('restaurant deck starts 0 saved', before.includes('0 SAVED'));

await drag(center, 220);
let after = await sectionText('restaurant_cards');
check('drag right shortlists (1 SAVED)', after.includes('1 SAVED'));
check('progress advances to 2 OF 3', after.includes('2 OF 3'));
check('shortlist chip appears', after.includes('Burma Burma'));

// --- drag left = pass, no save ---
center = await cardCenter('restaurant_cards');
await drag(center, -220);
after = await sectionText('restaurant_cards');
check('drag left passes (still 1 SAVED)', after.includes('1 SAVED'));
check('progress advances to 3 OF 3', after.includes('3 OF 3'));

// --- undo brings the passed card back ---
await page.$$eval('[data-fixture="restaurant_cards"] button', (btns) => {
  btns.find((b) => b.textContent.trim() === 'Undo')?.click();
});
await sleep(700);
after = await sectionText('restaurant_cards');
check('undo restores 2 OF 3', after.includes('2 OF 3'));

// --- tap to flip reveals spec sheet (assert on the TOP card's inner) ---
const topInnerTransform = () =>
  page.$eval('[data-fixture="restaurant_cards"]', (el) => {
    const cards = [...el.querySelectorAll('div')].filter((d) =>
      (d.getAttribute('class') || '').includes('touch-none'),
    );
    const top = cards.find((c) => c.style.pointerEvents === 'auto');
    if (!top) return 'no-top';
    const inner = [...top.querySelectorAll('div')].find((d) =>
      (d.getAttribute('class') || '').includes('preserve-3d'),
    );
    return inner ? getComputedStyle(inner).transform : 'no-inner';
  });

center = await cardCenter('restaurant_cards');
await page.mouse.click(center.x, center.y);
await sleep(700);
const flipped = await topInnerTransform();
check('tap flips card (rotateY matrix)', flipped.includes('matrix'), flipped.slice(0, 40));

await page.screenshot({ path: '/tmp/deck-flipped.png', clip: (await deckBox('restaurant_cards')) });

// flip back before button test
await page.mouse.click(center.x, center.y);
await sleep(700);
const unflipped = await topInnerTransform();
check('second tap flips back', unflipped === 'none' || unflipped === 'matrix(1, 0, 0, 1, 0, 0)', unflipped.slice(0, 40));

// --- shortlist round button, twice → deck completes ---
await page.$$eval('[data-fixture="restaurant_cards"] button[title="Shortlist"]', (btns) => btns[0]?.click());
await sleep(800);
after = await sectionText('restaurant_cards');
check('shortlist button saves (2 SAVED, 3 OF 3)', after.includes('2 SAVED') && after.includes('3 OF 3'));

await page.$$eval('[data-fixture="restaurant_cards"] button[title="Shortlist"]', (btns) => btns[0]?.click());
await sleep(900);
after = await sectionText('restaurant_cards');
check('end state appears', after.includes("That's the lot."), '');
check('end state counts', after.includes('3 reviewed · 3 shortlisted'));

await page.screenshot({ path: '/tmp/deck-end.png', clip: (await deckBox('restaurant_cards')) });

// --- "Deal again" restart ---
await page.$$eval('[data-fixture="restaurant_cards"] button', (btns) => {
  btns.find((b) => b.textContent.includes('Deal again'))?.click();
});
await sleep(700);
after = await sectionText('restaurant_cards');
check('deal again restarts deck', after.includes('1 OF 3') && after.includes('0 SAVED'));

// --- CTA fires a quick prompt into the log ---
await page.$$eval('[data-fixture="restaurant_cards"] button', (btns) => {
  btns.find((b) => b.textContent.trim() === 'View menu')?.click();
});
await sleep(400);
const log = await page.$eval('#prompt-log', (el) => el.textContent);
check('CTA fires exact menu prompt', log.includes('Show me the menu of Burma Burma on Zomato.'), log.slice(0, 80));

// --- zepto CTA prompt wording ---
await page.evaluate(() => {
  document.querySelector('[data-fixture="zepto_product_cards"]').scrollIntoView({ block: 'center' });
});
await sleep(400);
await page.$$eval('[data-fixture="zepto_product_cards"] button', (btns) => {
  btns.find((b) => b.textContent.trim() === 'Add to cart')?.click();
});
await sleep(400);
const log2 = await page.$eval('#prompt-log', (el) => el.textContent);
check('zepto CTA prompt matches grid wording', log2.includes('Add 1 Amul Gold Milk 1L to my Zepto cart.'), '');

// --- grid toggle swaps to classic grid and back ---
await page.$$eval('[data-fixture="flight_cards"] button', (btns) => {
  btns.find((b) => b.textContent.trim() === 'Grid')?.click();
});
await sleep(600);
let flightsText = await sectionText('flight_cards');
check('grid toggle shows classic flight grid', !flightsText.includes('SHORTLIST') && flightsText.includes('Flights'));
await page.$$eval('[data-fixture="flight_cards"] button', (btns) => {
  btns.find((b) => b.textContent.trim() === 'Deck')?.click();
});
await sleep(600);
flightsText = await sectionText('flight_cards');
check('toggle back to deck restores deck', flightsText.includes('1 OF 3'));

// --- screenshots: desktop overview + phone viewport ---
await page.evaluate(() => window.scrollTo(0, 0));
await sleep(300);
await page.screenshot({ path: '/tmp/deck-desktop.png', fullPage: false });

await page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true });
await page.evaluate(() => {
  document.querySelector('[data-fixture="flight_cards"]').scrollIntoView({ block: 'center' });
});
await sleep(600);
await page.screenshot({ path: '/tmp/deck-phone.png', fullPage: false });

const realErrors = consoleErrors.filter(
  (e) =>
    !e.includes('placehold.co') &&
    !e.includes('favicon') &&
    !e.includes('net::ERR') &&
    !e.includes('Failed to load resource'),
);
check('no console/page errors', realErrors.length === 0, realErrors.slice(0, 3).join(' | '));

await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
