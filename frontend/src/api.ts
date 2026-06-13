import { Capacitor } from '@capacitor/core';

const TOKEN_KEY = "companion_token";
const REFRESH_KEY = "companion_refresh_token";

// On web the app is same-origin with the API, so paths stay relative. In the
// native app the bundle is served from a local origin, so calls must target the
// deployed backend absolutely (CORS is open; auth is a Bearer header).
const API_BASE = Capacitor.isNativePlatform()
  ? ((import.meta.env.VITE_API_BASE as string | undefined) || 'https://app.jatayulabs.com')
  : '';

function apiUrl(path: string): string {
  return path.startsWith('/') ? `${API_BASE}${path}` : path;
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export function getRefreshToken() {
  return localStorage.getItem(REFRESH_KEY);
}

export function setRefreshToken(token: string) {
  localStorage.setItem(REFRESH_KEY, token);
}

export function clearRefreshToken() {
  localStorage.removeItem(REFRESH_KEY);
}

async function handleResponse(response: Response) {
  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text };
  }

  if (response.status === 401) {
    clearToken();
    clearRefreshToken();
    window.dispatchEvent(new CustomEvent('auth-failure'));
    throw new Error(data.detail || "Session expired. Please sign in again.");
  }

  if (!response.ok) {
    throw new Error(data.detail || data.message || response.statusText);
  }

  return data;
}

let refreshInFlight: Promise<boolean> | null = null;

/**
 * Exchanges the stored remember-me refresh token for a fresh access token.
 * Single-flight: concurrent 401s share one exchange. Resolves false when no
 * refresh token exists or the exchange fails (caller falls back to login).
 */
function tryRefresh(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      const refreshToken = getRefreshToken();
      if (!refreshToken) return false;
      try {
        const response = await fetch(apiUrl('/api/auth/refresh/'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!response.ok) return false;
        const data = await response.json();
        if (!data?.access_token) return false;
        setToken(data.access_token);
        if (data.refresh_token) setRefreshToken(data.refresh_token);
        return true;
      } catch {
        return false;
      }
    })();
    refreshInFlight.finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function request(path: string, init: RequestInit, retried = false): Promise<any> {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  };
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const response = await fetch(apiUrl(path), { ...init, headers });
  if (response.status === 401 && !retried && (await tryRefresh())) {
    return request(path, init, true);
  }
  return handleResponse(response);
}

export const api = {
  get(path: string) {
    return request(path, {});
  },

  post(path: string, body?: any) {
    return request(path, { method: 'POST', body: JSON.stringify(body) });
  },

  delete(path: string) {
    return request(path, { method: 'DELETE' });
  },
};
