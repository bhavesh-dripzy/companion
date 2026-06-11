import { Capacitor } from '@capacitor/core';

const TOKEN_KEY = "companion_token";

// On web the app is same-origin with the API, so paths stay relative. In the
// native app the bundle is served from a local origin, so calls must target the
// deployed backend absolutely (CORS is open; auth is a Bearer header).
const API_BASE = Capacitor.isNativePlatform()
  ? ((import.meta.env.VITE_API_BASE as string | undefined) || 'https://houzzat.in')
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
    window.dispatchEvent(new CustomEvent('auth-failure'));
    throw new Error("Session expired. Please sign in again.");
  }

  if (!response.ok) {
    throw new Error(data.detail || data.message || response.statusText);
  }

  return data;
}

export const api = {
  async get(path: string) {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const response = await fetch(apiUrl(path), { headers });
    return handleResponse(response);
  },

  async post(path: string, body?: any) {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const response = await fetch(apiUrl(path), {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });
    return handleResponse(response);
  },

  async delete(path: string) {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const response = await fetch(apiUrl(path), {
      method: 'DELETE',
      headers,
    });
    return handleResponse(response);
  }
};
