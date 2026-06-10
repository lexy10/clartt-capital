import { create } from 'zustand';
import { apiClient } from '../services/ApiClient';

// ---- Types ----

export type AgentState =
  | 'IDLE'
  | 'PLANNING'
  | 'EXECUTING'
  | 'WAITING_FOR_INPUT'
  | 'REVIEWING'
  | 'COMPLETED'
  | 'FAILED'
  | 'PAUSED';

export interface AgentInfo {
  name: string;
  description: string;
  state: AgentState;
  enabled: boolean;
  current_task_id: string | null;
  current_task_description: string | null;
  uptime: number;
  error_count: number;
  last_heartbeat: string | null;
  llm_cost_today_usd: number;
}

export interface ActivityEvent {
  id: string;
  type: string;
  agent_name: string;
  task_id?: string;
  message: string;
  timestamp: string;
}

export interface PendingApproval {
  id: string;
  agent_name: string;
  task_id: string;
  action_description: string;
  status: 'PENDING' | 'APPROVED' | 'DENIED' | 'EXPIRED';
  created_at: string;
}

export interface PipelineInfo {
  id: string;
  name: string;
  current_stage_index: number;
  stages: Array<{
    agent_name: string;
    task_type: string;
    status: string;
  }>;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface QueueDepth {
  CRITICAL: number;
  HIGH: number;
  NORMAL: number;
  LOW: number;
}

export interface LLMUsage {
  agent_name: string;
  daily_cost_usd: number;
  daily_tokens: number;
  budget_usd: number;
}

export type AutonomyMode = 'approval' | 'full_autonomy';

export interface EquityPauseStatus {
  account_id: string;
  account_label?: string;
  paused: boolean;
  current_drawdown_pct: number;
  high_water_mark: number;
  current_equity: number;
  max_drawdown_pct: number;
  recovery_pct: number;
}

export interface StrategyHealth {
  strategy_id: string;
  strategy_name: string;
  rolling_win_rate: number | null;
  rolling_profit_factor: number | null;
  backtest_win_rate: number | null;
  backtest_profit_factor: number | null;
  status: 'healthy' | 'warning' | 'decaying' | 'insufficient_data';
  trade_count: number;
  lookback_trades: number;
}

// ---- Store Interface ----

interface AgentStoreState {
  agents: AgentInfo[];
  killSwitchActive: boolean;
  activityEvents: ActivityEvent[];
  pendingApprovals: PendingApproval[];
  pipelines: PipelineInfo[];
  queueDepth: QueueDepth;
  llmUsage: LLMUsage[];
  autonomyMode: AutonomyMode;
  equityPauseStatuses: EquityPauseStatus[];
  strategyHealth: StrategyHealth[];
  loading: boolean;
  error: string | null;

  // Actions
  fetchAgents: () => Promise<void>;
  toggleKillSwitch: () => Promise<void>;
  toggleAgent: (name: string, enabled: boolean) => Promise<void>;
  startAgent: (name: string) => Promise<void>;
  stopAgent: (name: string) => Promise<void>;
  pauseAgent: (name: string) => Promise<void>;
  resumeAgent: (name: string) => Promise<void>;
  approveRequest: (approvalId: string) => Promise<void>;
  denyRequest: (approvalId: string, reason?: string) => Promise<void>;
  abortPipeline: (pipelineId: string) => Promise<void>;
  setAutonomyMode: (mode: AutonomyMode) => Promise<void>;
  deactivateEquityPause: (accountId: string) => Promise<void>;
  handleAgentEvent: (event: any) => void;
}

// Helper to make agent API calls via the backend proxy
const http = () => (apiClient as any).http;

export const useAgentStore = create<AgentStoreState>((set, get) => ({
  agents: [],
  killSwitchActive: false,
  activityEvents: [],
  pendingApprovals: [],
  pipelines: [],
  queueDepth: { CRITICAL: 0, HIGH: 0, NORMAL: 0, LOW: 0 },
  llmUsage: [],
  autonomyMode: 'approval',
  equityPauseStatuses: [],
  strategyHealth: [],
  loading: false,
  error: null,

  fetchAgents: async () => {
    set({ loading: true, error: null });
    try {
      const [agentsRes, metricsRes, approvalsRes, pipelinesRes, configRes, equityRes, healthRes] =
        await Promise.allSettled([
          http().get('/agents'),
          http().get('/agents/metrics'),
          http().get('/agents/approvals'),
          http().get('/agents/pipelines'),
          http().get('/agents/config'),
          http().get('/agents/equity-pause'),
          http().get('/agents/strategy-health'),
        ]);

      const agents = agentsRes.status === 'fulfilled' ? agentsRes.value.data : [];
      const metrics = metricsRes.status === 'fulfilled' ? metricsRes.value.data : {};
      const approvals = approvalsRes.status === 'fulfilled' ? approvalsRes.value.data : [];
      const pipelines = pipelinesRes.status === 'fulfilled' ? pipelinesRes.value.data : [];
      const config = configRes.status === 'fulfilled' ? configRes.value.data : {};
      const equityPause = equityRes.status === 'fulfilled' ? equityRes.value.data : [];
      const strategyHealthData = healthRes.status === 'fulfilled' ? healthRes.value.data : [];

      set({
        agents: Array.isArray(agents) ? agents : [],
        killSwitchActive: metrics.kill_switch_active ?? false,
        queueDepth: metrics.queue_depth ?? { CRITICAL: 0, HIGH: 0, NORMAL: 0, LOW: 0 },
        llmUsage: metrics.llm_usage ?? [],
        pendingApprovals: Array.isArray(approvals) ? approvals : [],
        pipelines: Array.isArray(pipelines) ? pipelines : [],
        autonomyMode: config.autonomy_mode ?? 'approval',
        equityPauseStatuses: Array.isArray(equityPause) ? equityPause : [],
        strategyHealth: Array.isArray(strategyHealthData) ? strategyHealthData : [],
        loading: false,
      });
    } catch (err) {
      set({ loading: false, error: err instanceof Error ? err.message : 'Failed to fetch agents' });
    }
  },

  toggleKillSwitch: async () => {
    const { killSwitchActive } = get();
    try {
      if (killSwitchActive) {
        await http().post('/agents/kill-switch/deactivate');
      } else {
        await http().post('/agents/kill-switch/activate');
      }
      set({ killSwitchActive: !killSwitchActive });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Kill switch operation failed' });
    }
  },

  toggleAgent: async (name: string, enabled: boolean) => {
    try {
      if (enabled) {
        await http().post(`/agents/${name}/enable`);
      } else {
        await http().post(`/agents/${name}/disable`);
      }
      set((state) => ({
        agents: state.agents.map((a) =>
          a.name === name ? { ...a, enabled } : a,
        ),
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Toggle agent failed' });
    }
  },

  startAgent: async (name: string) => {
    try {
      await http().post(`/agents/${name}/start`);
      set((state) => ({
        agents: state.agents.map((a) =>
          a.name === name ? { ...a, state: 'PLANNING' as AgentState } : a,
        ),
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Start agent failed' });
    }
  },

  stopAgent: async (name: string) => {
    try {
      await http().post(`/agents/${name}/stop`);
      set((state) => ({
        agents: state.agents.map((a) =>
          a.name === name ? { ...a, state: 'IDLE' as AgentState } : a,
        ),
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Stop agent failed' });
    }
  },

  pauseAgent: async (name: string) => {
    try {
      await http().post(`/agents/${name}/pause`);
      set((state) => ({
        agents: state.agents.map((a) =>
          a.name === name ? { ...a, state: 'PAUSED' as AgentState } : a,
        ),
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Pause agent failed' });
    }
  },

  resumeAgent: async (name: string) => {
    try {
      await http().post(`/agents/${name}/resume`);
      set((state) => ({
        agents: state.agents.map((a) =>
          a.name === name ? { ...a, state: 'EXECUTING' as AgentState } : a,
        ),
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Resume agent failed' });
    }
  },

  approveRequest: async (approvalId: string) => {
    try {
      await http().post(`/agents/approvals/${approvalId}/approve`);
      set((state) => ({
        pendingApprovals: state.pendingApprovals.filter((a) => a.id !== approvalId),
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Approve failed' });
    }
  },

  denyRequest: async (approvalId: string, reason?: string) => {
    try {
      await http().post(`/agents/approvals/${approvalId}/deny`, { reason: reason ?? '' });
      set((state) => ({
        pendingApprovals: state.pendingApprovals.filter((a) => a.id !== approvalId),
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Deny failed' });
    }
  },

  abortPipeline: async (pipelineId: string) => {
    try {
      await http().post(`/agents/pipelines/${pipelineId}/abort`);
      set((state) => ({
        pipelines: state.pipelines.map((p) =>
          p.id === pipelineId ? { ...p, status: 'ABORTED' } : p,
        ),
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Abort pipeline failed' });
    }
  },

  setAutonomyMode: async (mode: AutonomyMode) => {
    try {
      await http().put('/agents/config', { autonomy_mode: mode });
      set({ autonomyMode: mode });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Set autonomy mode failed' });
    }
  },

  deactivateEquityPause: async (accountId: string) => {
    try {
      await http().post(`/agents/equity-pause/${accountId}/deactivate`);
      set((state) => ({
        equityPauseStatuses: state.equityPauseStatuses.map((ep) =>
          ep.account_id === accountId ? { ...ep, paused: false } : ep,
        ),
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Deactivate equity pause failed' });
    }
  },

  handleAgentEvent: (event: any) => {
    if (!event || !event.type) return;

    const eventType: string = event.type;

    if (eventType === 'agent:stateChange') {
      set((state) => ({
        agents: state.agents.map((a) =>
          a.name === event.agent_name ? { ...a, state: event.new_state } : a,
        ),
      }));
    } else if (eventType === 'agent:killSwitch') {
      set({ killSwitchActive: event.active ?? event.is_active ?? false });
    } else if (eventType === 'agent:approvalRequested') {
      set((state) => ({
        pendingApprovals: [
          {
            id: event.approval_id,
            agent_name: event.agent_name,
            task_id: event.task_id,
            action_description: event.action_description,
            status: 'PENDING',
            created_at: event.timestamp ?? new Date().toISOString(),
          },
          ...state.pendingApprovals,
        ],
      }));
    } else if (eventType === 'agent:approvalResolved') {
      set((state) => ({
        pendingApprovals: state.pendingApprovals.filter((a) => a.id !== event.approval_id),
      }));
    } else if (eventType === 'agent:pipelineUpdate') {
      set((state) => ({
        pipelines: state.pipelines.map((p) =>
          p.id === event.pipeline_id
            ? { ...p, status: event.status, current_stage_index: event.current_stage_index ?? p.current_stage_index }
            : p,
        ),
      }));
    } else if (eventType === 'agent:taskUpdate') {
      set((state) => ({
        agents: state.agents.map((a) =>
          a.name === event.agent_name
            ? { ...a, current_task_id: event.task_id, current_task_description: event.description ?? a.current_task_description }
            : a,
        ),
      }));
    }

    // Always add to activity feed
    if (event.agent_name || event.type) {
      set((state) => ({
        activityEvents: [
          {
            id: event.id ?? `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            type: eventType,
            agent_name: event.agent_name ?? '',
            task_id: event.task_id,
            message: event.message ?? event.action_description ?? eventType,
            timestamp: event.timestamp ?? new Date().toISOString(),
          },
          ...state.activityEvents,
        ].slice(0, 200), // Keep last 200 events
      }));
    }
  },
}));
