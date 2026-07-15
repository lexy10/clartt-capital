import { create } from 'zustand';

/**
 * Theme preferences.
 *
 * Two independent axes:
 *   - mode:   light / dark / system   → controls the palette (data-theme)
 *   - accent: brand colour preset      → controls --accent* (data-accent)
 *
 * Preferences are per-user: they're stored in localStorage keyed by user id,
 * so different users signing in on the same browser each keep their own look.
 * A "last used" copy is also kept under a global key so the very first paint
 * (before we know who's logged in) matches what the user last chose — the
 * inline script in index.html reads that same global key to avoid a flash.
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

interface ThemeState extends ThemePrefs {
  /** User whose prefs are loaded (null = global/anonymous defaults). */
  userId: string | null;
  setMode: (mode: ThemeMode) => void;
  setAccent: (accent: Accent) => void;
  /** Load the given user's saved prefs (call when the logged-in user changes). */
  loadForUser: (userId: string | null) => void;
}

const initial: ThemePrefs = { ...DEFAULTS, ...readPrefs(GLOBAL_KEY) };
applyToDom(initial);

export const useThemeStore = create<ThemeState>((set, get) => {
  // Keep 'system' mode reactive to OS changes.
  if (typeof window !== 'undefined' && window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
      if (get().mode === 'system') applyToDom({ mode: get().mode, accent: get().accent });
    });
  }

  const persist = (prefs: ThemePrefs) => {
    applyToDom(prefs);
    writePrefs(GLOBAL_KEY, prefs);
    const { userId } = get();
    if (userId) writePrefs(userKey(userId), prefs);
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

    loadForUser: (userId) => {
      const saved = userId ? readPrefs(userKey(userId)) : readPrefs(GLOBAL_KEY);
      const prefs: ThemePrefs = { ...DEFAULTS, ...saved };
      applyToDom(prefs);
      writePrefs(GLOBAL_KEY, prefs); // keep first-paint key in sync with this user
      set({ userId, mode: prefs.mode, accent: prefs.accent });
    },
  };
});
