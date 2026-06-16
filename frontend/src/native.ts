import { Capacitor } from '@capacitor/core';
import { api } from './api';

export const isNative = Capacitor.isNativePlatform();

// The loopback redirect paste-mode providers emit. The port is mcp-remote's
// per-server hash port (e.g. Swiggy = 16148/21621), not a fixed one, so match
// any localhost port — the backend picks the port the server whitelists.
const CALLBACK_RE = /^https?:\/\/localhost:\d+\/oauth\/callback/;
const REDIRECT_SUCCESS = 'mcp_oauth=success';

interface IabRef {
  addEventListener(evt: string, cb: (e: { url?: string }) => void): void;
  close(): void;
}
interface CordovaIab {
  open(url: string, target: string, options?: string): IabRef;
}

function cordovaInAppBrowser(): CordovaIab | null {
  const cordova = (window as unknown as { cordova?: { InAppBrowser?: CordovaIab } }).cordova;
  return cordova?.InAppBrowser ?? null;
}

/**
 * Open the provider authorize URL in an in-app WebView and resolve once the
 * OAuth redirect is intercepted. `loadstart` fires at the START of every
 * navigation — including to the unreachable localhost callback — so the auth
 * code is captured before the dead load matters. `loaderror` is a backstop for
 * the same URL; `exit` means the user dismissed the sheet.
 */
function interceptOAuth(authorizationUrl: string): Promise<{ kind: 'paste' | 'redirect'; url: string }> {
  return new Promise((resolve, reject) => {
    const iab = cordovaInAppBrowser();
    if (!iab) {
      reject(new Error('In-app browser is unavailable in this build.'));
      return;
    }
    const ref = iab.open(
      authorizationUrl,
      '_blank',
      'location=yes,clearsessioncache=yes,clearcache=no,hidden=no',
    );
    let settled = false;
    const finish = (run: () => void) => {
      if (settled) return;
      settled = true;
      try {
        ref.close();
      } catch {
        /* already closed */
      }
      run();
    };

    const onNavigate = (e: { url?: string }) => {
      const url = e?.url || '';
      if (CALLBACK_RE.test(url)) {
        if (url.includes('error=')) {
          finish(() => reject(new Error('The provider denied authorization.')));
        } else {
          finish(() => resolve({ kind: 'paste', url }));
        }
      } else if (url.includes(REDIRECT_SUCCESS)) {
        finish(() => resolve({ kind: 'redirect', url }));
      }
    };

    ref.addEventListener('loadstart', onNavigate);
    ref.addEventListener('loaderror', onNavigate);
    ref.addEventListener('exit', () => {
      if (settled) return;
      settled = true;
      reject(new Error('cancelled'));
    });
  });
}

/**
 * Native MCP connect: negotiate the OAuth flow, run the provider login in an
 * intercepting WebView, and finish the token exchange — no copy-paste, no
 * leaving the app. Falls back to the returned status when the backend hands
 * back a non-OAuth flow (e.g. pairing or a provider that needs no OAuth).
 */
export async function runNativeMcpConnect(provider: string): Promise<{ status: string; detail: string }> {
  const start = await api.get(`/api/mcp/oauth/start?provider=${provider}`);
  if (!start.authorization_url) {
    return {
      status: start.status || 'unsupported',
      detail: start.detail || 'This provider cannot be connected from the app yet.',
    };
  }
  if (start.completion === 'external') {
    // Login completes on the provider's own site (e.g. Zerodha) — no redirect
    // to intercept; status flips server-side once the user finishes.
    window.open(start.authorization_url, '_blank');
    return {
      status: 'external_started',
      detail: start.detail || 'Finish logging in, then refresh the connection status.',
    };
  }
  const caught = await interceptOAuth(start.authorization_url);
  if (caught.kind === 'redirect') {
    return { status: 'connected_pending_test', detail: `${start.provider_label || 'Provider'} connected.` };
  }
  const done = await api.post('/api/mcp/oauth/complete/', { text: caught.url });
  return { status: done.status || 'connected_pending_test', detail: done.detail || 'Connected.' };
}
