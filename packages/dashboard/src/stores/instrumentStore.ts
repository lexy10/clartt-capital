import { create } from 'zustand';
import { apiClient } from '../services/ApiClient';
import type { Instrument, CreateInstrumentDto, UpdateInstrumentDto } from '../types/api';

interface InstrumentState {
  instruments: Instrument[];
  loading: boolean;
  error: string | null;
  fetchInstruments: (includeInactive?: boolean) => Promise<void>;
  createInstrument: (dto: CreateInstrumentDto) => Promise<void>;
  updateInstrument: (id: string, dto: UpdateInstrumentDto) => Promise<void>;
  deleteInstrument: (id: string) => Promise<void>;
}

export const useInstrumentStore = create<InstrumentState>((set) => ({
  instruments: [],
  loading: false,
  error: null,

  fetchInstruments: async (includeInactive?: boolean) => {
    set({ loading: true, error: null });
    try {
      const instruments = await apiClient.instruments.list(includeInactive);
      set({ instruments, loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch instruments';
      set({ error: message, loading: false });
    }
  },

  createInstrument: async (dto: CreateInstrumentDto) => {
    set({ error: null });
    try {
      const instrument = await apiClient.instruments.create(dto);
      set((state) => ({ instruments: [...state.instruments, instrument] }));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create instrument';
      set({ error: message });
    }
  },

  updateInstrument: async (id: string, dto: UpdateInstrumentDto) => {
    set({ error: null });
    try {
      const updated = await apiClient.instruments.update(id, dto);
      set((state) => ({
        instruments: state.instruments.map((i) => (i.id === id ? updated : i)),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update instrument';
      set({ error: message });
    }
  },

  deleteInstrument: async (id: string) => {
    set({ error: null });
    try {
      await apiClient.instruments.delete(id);
      set((state) => ({
        instruments: state.instruments.filter((i) => i.id !== id),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete instrument';
      set({ error: message });
    }
  },
}));
