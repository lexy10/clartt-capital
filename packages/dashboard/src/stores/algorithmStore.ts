import { create } from 'zustand';
import type { AlgorithmInfo, AlgorithmSource } from '../types/api';
import { apiClient } from '../services/ApiClient';

interface AlgorithmState {
  algorithms: AlgorithmInfo[];
  loading: boolean;
  error: string | null;
  selectedAlgorithm: string | null;
  source: AlgorithmSource | null;
  sourceLoading: boolean;
  uploading: boolean;
  uploadError: string | null;
  saving: boolean;
  saveError: string | null;

  fetchAlgorithms: () => Promise<void>;
  fetchSource: (name: string) => Promise<void>;
  uploadAlgorithm: (file: File) => Promise<string | null>;
  updateSource: (name: string, source: string) => Promise<boolean>;
  deleteAlgorithm: (name: string) => Promise<void>;
  selectAlgorithm: (name: string | null) => void;
  clearSource: () => void;
}

export const useAlgorithmStore = create<AlgorithmState>((set) => ({
  algorithms: [],
  loading: false,
  error: null,
  selectedAlgorithm: null,
  source: null,
  sourceLoading: false,
  uploading: false,
  uploadError: null,
  saving: false,
  saveError: null,

  fetchAlgorithms: async () => {
    set({ loading: true, error: null });
    try {
      const algorithms = await apiClient.strategies.getAlgorithms();
      set({ algorithms, loading: false });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to fetch algorithms', loading: false });
    }
  },

  fetchSource: async (name: string) => {
    set({ sourceLoading: true });
    try {
      const source = await apiClient.strategies.getAlgorithmSource(name);
      set({ source, sourceLoading: false });
    } catch {
      set({ source: null, sourceLoading: false });
    }
  },

  uploadAlgorithm: async (file: File) => {
    set({ uploading: true, uploadError: null });
    try {
      const result = await apiClient.strategies.uploadAlgorithm(file);
      // Refresh the list
      const algorithms = await apiClient.strategies.getAlgorithms();
      set({ algorithms, uploading: false });
      return result.name;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Upload failed';
      set({ uploadError: msg, uploading: false });
      return null;
    }
  },

  updateSource: async (name: string, source: string) => {
    set({ saving: true, saveError: null });
    try {
      await apiClient.strategies.updateAlgorithmSource(name, source);
      // Refresh algorithms list and source
      const algorithms = await apiClient.strategies.getAlgorithms();
      const updated = await apiClient.strategies.getAlgorithmSource(name);
      set({ algorithms, source: updated, saving: false });
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Save failed';
      set({ saveError: msg, saving: false });
      return false;
    }
  },

  deleteAlgorithm: async (name: string) => {
    await apiClient.strategies.deleteAlgorithm(name);
    set((state) => ({
      algorithms: state.algorithms.filter((a) => a.name !== name),
      selectedAlgorithm: state.selectedAlgorithm === name ? null : state.selectedAlgorithm,
      source: state.selectedAlgorithm === name ? null : state.source,
    }));
  },

  selectAlgorithm: (name: string | null) => {
    set({ selectedAlgorithm: name, source: null });
  },

  clearSource: () => {
    set({ source: null });
  },
}));
