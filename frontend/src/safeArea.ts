import { Capacitor, SystemBars, SystemBarsStyle } from '@capacitor/core';

type InsetEdge = 'top' | 'right' | 'bottom' | 'left';

const EDGES: InsetEdge[] = ['top', 'right', 'bottom', 'left'];

const PADDING_PROP: Record<InsetEdge, 'paddingTop' | 'paddingRight' | 'paddingBottom' | 'paddingLeft'> = {
  top: 'paddingTop',
  right: 'paddingRight',
  bottom: 'paddingBottom',
  left: 'paddingLeft',
};

let envProbe: HTMLDivElement | null = null;

function readEnvInset(edge: InsetEdge): number {
  if (!envProbe) {
    envProbe = document.createElement('div');
    envProbe.setAttribute('aria-hidden', 'true');
    envProbe.style.cssText =
      'position:fixed;top:0;left:0;width:0;height:0;visibility:hidden;pointer-events:none;';
    document.documentElement.appendChild(envProbe);
  }
  const prop = PADDING_PROP[edge];
  envProbe.style[prop] = `env(safe-area-inset-${edge}, 0px)`;
  return parseFloat(getComputedStyle(envProbe)[prop]) || 0;
}

function readInjectedInset(edge: InsetEdge): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(`--safe-area-inset-${edge}`);
  return parseFloat(raw) || 0;
}

const _VP_DEBUG =
  typeof location !== 'undefined' && location.search.includes('vpdebug');
let _badge: HTMLDivElement | null = null;

function _drawDebug(vv: VisualViewport | null | undefined): void {
  if (!_VP_DEBUG || typeof document === 'undefined' || !document.body) return;
  if (!_badge) {
    _badge = document.createElement('div');
    _badge.style.cssText =
      'position:fixed;top:4px;left:4px;z-index:99999;background:rgba(0,0,0,.82);color:#0f0;' +
      'font:11px/1.35 monospace;padding:5px 7px;border-radius:6px;pointer-events:none;white-space:pre';
    document.body.appendChild(_badge);
  }
  const appH = getComputedStyle(document.documentElement).getPropertyValue('--app-height').trim();
  _badge.textContent =
    `vv.height=${Math.round(vv?.height || 0)}\n` +
    `vv.offsetTop=${Math.round(vv?.offsetTop || 0)}\n` +
    `innerHeight=${window.innerHeight}\n` +
    `--app-height=${appH}`;
}

/** Pin the app shell to the VISUAL VIEWPORT (height + top) and keep --safe-* in sync.
 *
 *  The composer-above-keyboard problem on iOS Safari: Safari ignores the `interactive-widget`
 *  viewport property and does NOT shrink the layout viewport / dvh when the keyboard opens, so a
 *  bottom-anchored composer ends up behind it. The reliable fix is to bind the shell directly to
 *  `window.visualViewport` — which IS the visible area above the keyboard — and let #root be
 *  position:fixed at {top: offsetTop, height: vv.height}. No gap heuristic, no dvh guesswork. */
export function syncSafeAreaInsets(): void {
  const vv = window.visualViewport;
  if (vv) {
    const r = document.documentElement.style;
    r.setProperty('--app-height', `${Math.round(vv.height)}px`);
    r.setProperty('--app-vtop', `${Math.round(vv.offsetTop)}px`);
  }
  _drawDebug(vv);

  // Real safe insets come ONLY from env() (iOS notch / home indicator / rounded corners) and native
  // injection (Capacitor SystemBars on Android edge-to-edge) — NEVER visualViewport offsets (those
  // shift with scroll/keyboard and would bake a transient gap into --safe-*).
  for (const edge of EDGES) {
    const next = Math.max(readEnvInset(edge), readInjectedInset(edge));
    if (next > 0) {
      document.documentElement.style.setProperty(`--safe-area-inset-${edge}`, `${next}px`);
    }
  }
}

/** Wire safe-area updates for the app lifetime (call once from main.tsx). */
export function installSafeAreaInsets(): void {
  const run = () => syncSafeAreaInsets();

  run();
  window.addEventListener('resize', run, { passive: true });
  window.addEventListener('orientationchange', () => window.setTimeout(run, 100), { passive: true });
  window.visualViewport?.addEventListener('resize', run, { passive: true });
  window.visualViewport?.addEventListener('scroll', run, { passive: true });

  // Capacitor SystemBars may inject after first paint on Android edge-to-edge.
  for (const delay of [0, 50, 250, 1000]) {
    window.setTimeout(run, delay);
  }

  if (Capacitor.isNativePlatform()) {
    void SystemBars.setStyle({ style: SystemBarsStyle.Default }).catch(() => undefined);
  }
}
