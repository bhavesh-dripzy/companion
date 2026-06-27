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

function readViewportInsets(): Record<InsetEdge, number> {
  const vv = window.visualViewport;
  if (!vv) return { top: 0, right: 0, bottom: 0, left: 0 };
  return {
    top: Math.max(0, Math.round(vv.offsetTop)),
    right: Math.max(0, Math.round(window.innerWidth - vv.width - vv.offsetLeft)),
    bottom: Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop)),
    left: Math.max(0, Math.round(vv.offsetLeft)),
  };
}

/** Keep --safe-area-inset-* and --app-height in sync with env(), native injection, and the visual
 *  viewport (which shrinks when the on-screen keyboard opens). */
export function syncSafeAreaInsets(): void {
  const vv = window.visualViewport;
  // Shrink the shell to the visible height ONLY when the keyboard is OPEN (a big gap between the
  // layout viewport and the visual viewport). Then the header + composer stay put above the keyboard
  // and only the message list scrolls, like the APK. With NO keyboard we keep the full dynamic
  // viewport (100dvh) — visualViewport.height can read short on iOS otherwise, leaving an empty strip.
  const gap = vv ? Math.round(window.innerHeight - vv.height) : 0;
  const keyboardOpen = !!vv && gap > 120;
  document.documentElement.style.setProperty(
    '--app-height',
    keyboardOpen ? `${Math.round(vv!.height)}px` : '100dvh',
  );

  const viewport = readViewportInsets();
  for (const edge of EDGES) {
    // The viewport-derived BOTTOM inset IS the on-screen keyboard, not a real safe inset — and the
    // shell already excludes the keyboard via --app-height, so folding it into --safe-bottom would
    // double-pad (and displace) the composer. Bottom uses env()/native injection only.
    const candidates =
      edge === 'bottom'
        ? [readEnvInset(edge), readInjectedInset(edge)]
        : [readEnvInset(edge), readInjectedInset(edge), viewport[edge]];
    const next = Math.max(...candidates);
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
