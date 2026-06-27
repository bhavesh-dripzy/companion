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
  // When the keyboard closes, iOS can leave the LAYOUT viewport scrolled (it scrolled to reveal the
  // focused input) — which shows up as a blank strip at the top with the shell pushed down. Reset it.
  if (!keyboardOpen && (window.scrollY || window.scrollX)) {
    window.scrollTo(0, 0);
  }

  // Real safe insets come ONLY from env() (iOS notch / home indicator / rounded corners) and native
  // injection (Capacitor SystemBars on Android edge-to-edge). We deliberately do NOT fold in
  // visualViewport offsets: offsetTop/height shift with scroll + the keyboard, so baking them into
  // --safe-* (which the no-reset guard then never clears) left the shell permanently displaced.
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
