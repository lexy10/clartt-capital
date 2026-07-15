import { create } from 'zustand';
import { apiClient } from '../services/ApiClient';

export interface CurrentUser {
  id: string;
  email: string;
  role: string; // 'admin' | 'trader' | ...
  theme?: { mode?: string; accent?: string } | null;
}

interface AuthState {
  isAuthenticated: boolean;
  error: string | null;
  loading: boolean;

  /** Logged-in user (null until fetched). */
  currentUser: CurrentUser | null;

  /** All users, only populated for admins. Drives the top-bar user switcher. */
  allUsers: CurrentUser[];

  /** Which user the admin is currently "viewing as".
   *  null = view as self. Persists across reloads via localStorage. */
  viewingAsUserId: string | null;

  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  checkAuth: () => void;
  fetchCurrentUser: () => Promise<void>;
  setViewingAsUserId: (id: string | null) => void;
}

const VIEWING_AS_KEY = 'dashboard:viewingAsUserId';

function loadViewingAs(): string | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage.getItem(VIEWING_AS_KEY) : null;
  } catch {
    return null;
  }
}

export const useAuthStore = create<AuthState>((set, get) => ({
  isAuthenticated: !!localStorage.getItem('us30_access_token'),
  error: null,
  loading: false,
  currentUser: null,
  allUsers: [],
  viewingAsUserId: loadViewingAs(),

  login: async (email: string, password: string) => {
    set({ loading: true, error: null });
    try {
      await apiClient.auth.login({ email, password });
      set({ isAuthenticated: true, loading: false });
      get().fetchCurrentUser();
    } catch (err: any) {
      const message = err?.response?.data?.message || 'Invalid credentials';
      set({ error: message, loading: false });
    }
  },

  logout: () => {
    apiClient.auth.logout().catch(() => {});
    localStorage.removeItem('us30_access_token');
    localStorage.removeItem('us30_refresh_token');
    try { localStorage.removeItem(VIEWING_AS_KEY); } catch { /* ignore */ }
    set({
      isAuthenticated: false,
      currentUser: null,
      allUsers: [],
      viewingAsUserId: null,
    });
  },

  checkAuth: () => {
    const authed = !!localStorage.getItem('us30_access_token');
    set({ isAuthenticated: authed });
    if (authed && !get().currentUser) {
      get().fetchCurrentUser();
    }
  },

  fetchCurrentUser: async () => {
    try {
      const me = await apiClient.users.me();
      set({ currentUser: me });
      // Only the super admin pulls the user list (for the switcher + user
      // management); listAll is super-admin-gated on the backend.
      if (me.role === 'superadmin') {
        try {
          const all = await apiClient.users.listAll();
          set({ allUsers: all });
        } catch {
          // Permission or network error — leave list empty, dropdown won't render
        }
      }
    } catch {
      // Stale token — interceptor handles the 401
    }
  },

  setViewingAsUserId: (id: string | null) => {
    set({ viewingAsUserId: id });
    try {
      if (id) {
        localStorage.setItem(VIEWING_AS_KEY, id);
      } else {
        localStorage.removeItem(VIEWING_AS_KEY);
      }
    } catch { /* ignore */ }
  },
}));
