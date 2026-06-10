import { create } from 'zustand';

export type ConnectionStatus = 'connected' | 'disconnected' | 'reconnecting';

interface ConnectionState {
  status: ConnectionStatus;
  setStatus: (status: ConnectionStatus) => void;
}

export const useConnectionStore = create<ConnectionState>((set) => ({
  status: 'disconnected',
  setStatus: (status) => set({ status }),
}));
