/**
 * Hero-greeting prefetch + cache. The AI greeting is fetched once per app
 * load and shared: Login / App boot kick the request off as soon as a token
 * exists, and ChatArea consumes the same in-flight promise instead of
 * starting its own — so the hero is usually ready before it renders. The
 * last generated line is cached per user for instant first paint.
 */

import { api } from './api';

const LAST_USER_KEY = 'companion_last_user';

const cacheKey = (username?: string | null) =>
  `companion.heroGreeting.${(username || 'anon').trim()}`;

export function readCachedGreeting(username?: string | null): string {
  try {
    return localStorage.getItem(cacheKey(username)) || '';
  } catch {
    return '';
  }
}

export function rememberLastUser(username: string) {
  try {
    localStorage.setItem(LAST_USER_KEY, username);
  } catch {
    /* private mode — skip */
  }
}

export function lastKnownUser(): string {
  try {
    return localStorage.getItem(LAST_USER_KEY) || '';
  } catch {
    return '';
  }
}

let inflight: Promise<string | null> | null = null;
let inflightFor = '';

export function prefetchHeroGreeting(username?: string | null): Promise<string | null> {
  const user = (username || 'anon').trim();
  if (!inflight || inflightFor !== user) {
    inflightFor = user;
    inflight = api
      .get('/api/hero/greeting/')
      .then((res: { greeting?: string }) => {
        const greeting = res?.greeting || null;
        if (greeting) {
          try {
            localStorage.setItem(cacheKey(user), greeting);
          } catch {
            /* private mode — skip */
          }
        }
        return greeting;
      })
      .catch(() => null);
  }
  return inflight;
}
