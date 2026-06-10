import { create } from 'zustand';
import { apiClient } from '../services/ApiClient';
import { wsManager } from '../services/WebSocketManager';

export interface CircuitBreakerInfo {
  name: string;
  service: string;
  state: 'closed' | 'open' | 'half_open';
  failureCount: number;
  lastStateChange: string;
  protectedDependency: string;
}

export interface ConsumerLagInfo {
  stream: string;
  group: string;
  lag: number;
  threshold: number;
  timestamp: string;
}

export interface ServiceDependency {
  name: string;
  status: string;
  circuitBreakerState?: string;
  lastSuccessfulContact: string;
}

export interface ServiceHealth {
  service: string;
  status: 'healthy' | 'degraded' | 'unhealthy';
  timestamp: string;
  dependencies: ServiceDependency[];
}

interface HealthState {
  circuitBreakers: CircuitBreakerInfo[];
  consumerLags: ConsumerLagInfo[];
  services: ServiceHealth[];
  warningBannerVisible: boolean;
  loading: boolean;
  lastRefresh: string | null;
  updateCircuitBreakerState: (payload: { name: string; previousState: string; newState: string; timestamp: string }) => void;
  updateConsumerLag: (payload: ConsumerLagInfo) => void;
  fetchHealthSnapshot: () => Promise<void>;
  subscribeToHealth: () => void;
}

export const useHealthStore = create<HealthState>((set, get) => ({
  circuitBreakers: [],
  consumerLags: [],
  services: [],
  warningBannerVisible: false,
  loading: false,
  lastRefresh: null,

  updateCircuitBreakerState: (payload) => {
    set((state) => {
      const updated = state.circuitBreakers.map((cb) =>
        cb.name === payload.name
          ? { ...cb, state: payload.newState as CircuitBreakerInfo['state'], lastStateChange: payload.timestamp }
          : cb,
      );
      const hasOpen = updated.some((cb) => cb.state === 'open');
      return { circuitBreakers: updated, warningBannerVisible: hasOpen };
    });
  },

  updateConsumerLag: (payload) => {
    set((state) => {
      const existing = state.consumerLags.filter(
        (l) => !(l.stream === payload.stream && l.group === payload.group),
      );
      return { consumerLags: [...existing, payload] };
    });
  },

  fetchHealthSnapshot: async () => {
    set({ loading: true });
    try {
      const [backendHealth, cbData, strategyHealth, executionHealth] = await Promise.allSettled([
        apiClient.health.getStatus(),
        apiClient.health.getCircuitBreakers(),
        apiClient.health.getStrategyEngineHealth(),
        apiClient.health.getExecutionEngineHealth(),
      ]);

      const services: ServiceHealth[] = [];

      if (backendHealth.status === 'fulfilled') {
        services.push(backendHealth.value as ServiceHealth);
      } else {
        services.push({ service: 'backend', status: 'unhealthy', timestamp: new Date().toISOString(), dependencies: [] });
      }

      if (strategyHealth.status === 'fulfilled') {
        services.push(strategyHealth.value as ServiceHealth);
      } else {
        services.push({ service: 'strategy-engine', status: 'unhealthy', timestamp: new Date().toISOString(), dependencies: [] });
      }

      if (executionHealth.status === 'fulfilled') {
        services.push(executionHealth.value as ServiceHealth);
      } else {
        services.push({ service: 'execution-engine', status: 'unhealthy', timestamp: new Date().toISOString(), dependencies: [] });
      }

      let allBreakers: CircuitBreakerInfo[] = [];
      if (cbData.status === 'fulfilled') {
        const data = cbData.value as { circuitBreakers: CircuitBreakerInfo[]; remoteBreakers: CircuitBreakerInfo[] };
        allBreakers = [...(data.circuitBreakers ?? []), ...(data.remoteBreakers ?? [])];
      }

      const hasOpen = allBreakers.some((cb) => cb.state === 'open');

      set({
        services,
        circuitBreakers: allBreakers,
        warningBannerVisible: hasOpen,
        loading: false,
        lastRefresh: new Date().toISOString(),
      });
    } catch {
      set({ loading: false });
    }
  },

  subscribeToHealth: () => {
    wsManager.subscribe('circuitBreaker_stateChange', (payload: unknown) => {
      get().updateCircuitBreakerState(payload as any);
    });
    wsManager.subscribe('consumerLag_alert', (payload: unknown) => {
      get().updateConsumerLag(payload as ConsumerLagInfo);
    });
  },
}));
