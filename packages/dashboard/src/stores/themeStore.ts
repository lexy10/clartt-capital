import { create } from 'zustand';
import { apiClient } from '../services/ApiClient';

/**
 * Theme preferences.
 *
 * Two independent axes:
 *   - mode:   light / dark / system   → controls the palette (data-theme)
 *   - accent: brand colour preset      → controls --accent* (data-accent)
 *
 * Preferences are per-user and persisted to the user's DB record (PUT
 * /users/me), so they follow the user across devices and browsers. localStorage
 * is used as a fast cache: a per-user key, plus a global "last used" key that
 * the inline script in index.html reads to paint the right theme before React
 * (and the /users/me response) are ready — avoiding a flash.
 *
 * On login we prefer the DB value; if the user has never set one we fall back
 * to the cached localStorage value, then to the defaults.
 */

export type ThemeMode = 'light' | 'dark' | 'system';
export type Accent = 'indigo' | 'emerald' | 'sky' | 'amber' | 'rose';

export const ACCENTS: { id: Accent; label: string; swatch: string }[] = [
  { id: 'indigo', label: 'Indigo', swatch: '#818cf8' },
  { id: 'emerald', label: 'Emerald', swatch: '#34d399' },
  { id: 'sky', label: 'Sky', swatch: '#38bdf8' },
  { id: 'amber', label: 'Amber', swatch: '#fbbf24' },
  { id: 'rose', label: 'Rose', swatch: '#fb7185' },
];

interface ThemePrefs {
  mode: ThemeMode;
  accent: Accent;
}

const GLOBAL_KEY = 'dashboard:theme'; // last-used, drives first paint
const userKey = (userId: string) => `dashboard:theme:${userId}`;

const DEFAULTS: ThemePrefs = { mode: 'dark', accent: 'indigo' };

function readPrefs(key: string): Partial<ThemePrefs> | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as Partial<ThemePrefs>) : null;
  } catch {
    return null;
  }
}

function writePrefs(key: string, prefs: ThemePrefs) {
  try {
    localStorage.setItem(key, JSON.stringify(prefs));
  } catch {
    /* storage unavailable — theme just won't persist */
  }
}

/** Resolve 'system' to a concrete palette using the OS preference. */
function resolveMode(mode: ThemeMode): 'light' | 'dark' {
  if (mode === 'system') {
    return typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-color-scheme: light)').matches
      ? 'light'
      : 'dark';
  }
  return mode;
}

/** Stamp the resolved theme onto <html> so the CSS variables switch. */
function applyToDom(prefs: ThemePrefs) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.setAttribute('data-theme', resolveMode(prefs.mode));
  root.setAttribute('data-accent', prefs.accent);
}

/** Sanitise arbitrary DB/localStorage input into valid prefs (defaults for the
 *  rest). Guards against a value the current build no longer recognises. */
function coercePrefs(raw: { mode?: string; accent?: string } | null | undefined): ThemePrefs {
  const mode: ThemeMode = raw?.mode === 'light' || raw?.mode === 'dark' || raw?.mode === 'system'
    ? raw.mode : DEFAULTS.mode;
  const accent: Accent = ACCENTS.some((a) => a.id === raw?.accent)
    ? (raw!.accent as Accent) : DEFAULTS.accent;
  return { mode, accent };
}

interface ThemeState extends ThemePrefs {
  /** User whose prefs are loaded (null = global/anonymous defaults). */
  userId: string | null;
  setMode: (mode: ThemeMode) => void;
  setAccent: (accent: Accent) => void;
  /** Load a user's saved prefs when the logged-in user changes. `dbPrefs` is
   *  the theme stored on the user record (from /users/me); it wins over the
   *  local cache so the choice follows the user across devices. */
  loadForUser: (userId: string | null, dbPrefs?: { mode?: string; accent?: string } | null) => void;
}

const initial: ThemePrefs = coercePrefs(readPrefs(GLOBAL_KEY));
applyToDom(initial);

export const useThemeStore = create<ThemeState>((set, get) => {
  // Keep 'system' mode reactive to OS changes.
  if (typeof window !== 'undefined' && window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
      if (get().mode === 'system') applyToDom({ mode: get().mode, accent: get().accent });
    });
  }

  // Debounced write-back to the DB so rapid toggles collapse into one request.
  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  const saveToServer = (prefs: ThemePrefs) => {
    if (!get().userId) return; // not logged in — local cache only
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      apiClient.users.updateMe({ theme: prefs }).catch(() => {
        /* offline / transient — the localStorage cache still holds the choice */
      });
    }, 400);
  };

  const persist = (prefs: ThemePrefs) => {
    applyToDom(prefs);
    writePrefs(GLOBAL_KEY, prefs);
    const { userId } = get();
    if (userId) writePrefs(userKey(userId), prefs);
    saveToServer(prefs);
  };

  return {
    ...initial,
    userId: null,

    setMode: (mode) => {
      const next = { mode, accent: get().accent };
      persist(next);
      set({ mode });
    },

    setAccent: (accent) => {
      const next = { mode: get().mode, accent };
      persist(next);
      set({ accent });
    },

    loadForUser: (userId, dbPrefs) => {
      // Priority: DB (follows the user everywhere) → local cache → defaults.
      const hasDb = dbPrefs && (dbPrefs.mode || dbPrefs.accent);
      const source = hasDb ? dbPrefs : userId ? readPrefs(userKey(userId)) : readPrefs(GLOBAL_KEY);
      const prefs = coercePrefs(source);
      applyToDom(prefs);
      writePrefs(GLOBAL_KEY, prefs); // keep first-paint key in sync with this user
      if (userId) writePrefs(userKey(userId), prefs);
      set({ userId, mode: prefs.mode, accent: prefs.accent });
    },
  };
});
