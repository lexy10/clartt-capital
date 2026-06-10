import { type FC, useEffect, useState, useCallback } from 'react';
import { useAgentStore } from '../stores/agentStore';
import type {
  AgentInfo,
  AgentState,
  AutonomyMode,
  EquityPauseStatus,
  StrategyHealth,
} from '../stores/agentStore';
import { wsManager } from '../services/WebSocketManager';

// ---- Helpers ----

const STATE_COLORS: Record<AgentState, string> = {
  IDLE: 'var(--text-muted)',
  PLANNING: 'var(--info, #3b82f6)',
  EXECUTING: 'var(--success)',
  WAITING_FOR_INPUT: 'var(--warning, #f59e0b)',
  REVIEWING: 'var(--info, #3b82f6)',
  COMPLETED: 'var(--success)',
  FAILED: 'var(--danger)',
  PAUSED: 'var(--warning, #f59e0b)',
};

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

type Tab = 'activity' | 'pipelines' | 'approvals' | 'queue' | 'costs';

// ---- Component ----

const AgentsPage: FC = () => {
  const {
    agents,
    killSwitchActive,
    activityEvents,
    pendingApprovals,
    pipelines,
    queueDepth,
    llmUsage,
    autonomyMode,
    equityPauseStatuses,
    strategyHealth,
    loading,
    error,
    fetchAgents,
    toggleKillSwitch,
    toggleAgent,
    startAgent,
    stopAgent,
    pauseAgent,
    resumeAgent,
    approveRequest,
    denyRequest,
    abortPipeline,
    setAutonomyMode,
    deactivateEquityPause,
    handleAgentEvent,
  } = useAgentStore();

  const [activeTab, setActiveTab] = useState<Tab>('activity');
  const [killSwitchLoading, setKillSwitchLoading] = useState(false);

  // Fetch initial data
  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  // Subscribe to WebSocket agent events
  useEffect(() => {
    wsManager.emit('subscribeAgents');

    const events = [
      'agent:stateChange',
      'agent:taskUpdate',
      'agent:pipelineUpdate',
      'agent:approvalRequested',
      'agent:approvalResolved',
      'agent:killSwitch',
      'agent:error',
      'agent:activity',
    ];

    const subIds: string[] = [];
    for (const evt of events) {
      const id = wsManager.subscribe(evt as any, (data: any) => {
        handleAgentEvent({ ...data, type: evt });
      });
      subIds.push(id);
    }

    return () => {
      wsManager.emit('unsubscribeAgents');
      subIds.forEach((id) => wsManager.unsubscribe(id));
    };
  }, [handleAgentEvent]);

  const handleKillSwitch = useCallback(async () => {
    setKillSwitchLoading(true);
    await toggleKillSwitch();
    setKillSwitchLoading(false);
  }, [toggleKillSwitch]);

  const totalDailyCost = llmUsage.reduce((sum, u) => sum + u.daily_cost_usd, 0);
  const totalBudget = llmUsage.reduce((sum, u) => sum + u.budget_usd, 0);

  return (
    <div style={{ padding: '20px 24px', maxWidth: 1400 }}>
      {error && (
        <div className="badge badge-danger" style={{ marginBottom: 12, display: 'block', textAlign: 'center', padding: '8px 12px' }}>
          {error}
        </div>
      )}

      {/* Top Controls: Kill Switch + Autonomy Mode + Cost Summary */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap', alignItems: 'stretch' }}>
        {/* Kill Switch */}
        <div className="card" style={{ flex: '0 0 auto', minWidth: 220 }}>
          <div className="card-header">
            <span className="card-title">Agent Kill Switch</span>
            <span className={`badge ${killSwitchActive ? 'badge-danger' : 'badge-success'}`}>
              {killSwitchActive ? 'Active' : 'Off'}
            </span>
          </div>
          <div style={{
            padding: '8px 12px',
            background: killSwitchActive ? 'var(--danger-bg)' : 'var(--success-bg)',
            borderRadius: 'var(--radius-md)',
            fontSize: 11,
            fontWeight: 500,
            color: killSwitchActive ? 'var(--danger)' : 'var(--success)',
            marginBottom: 8,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}>
            <span style={{
              width: 7, height: 7, borderRadius: '50%',
              background: killSwitchActive ? 'var(--danger)' : 'var(--success)',
              boxShadow: `0 0 6px ${killSwitchActive ? 'var(--danger)' : 'var(--success)'}`,
            }} />
            {killSwitchActive ? 'All agents halted' : 'Agents operational'}
          </div>
          <button
            onClick={handleKillSwitch}
            disabled={killSwitchLoading}
            style={{
              width: '100%', padding: '8px',
              borderRadius: 'var(--radius-md)',
              border: `1px solid ${killSwitchActive ? 'var(--success-border)' : 'var(--danger-border)'}`,
              background: killSwitchActive ? 'var(--success-bg)' : 'var(--danger-bg)',
              color: killSwitchActive ? 'var(--success)' : 'var(--danger)',
              fontSize: 11, fontWeight: 600, cursor: killSwitchLoading ? 'not-allowed' : 'pointer',
              opacity: killSwitchLoading ? 0.5 : 1,
            }}
          >
            {killSwitchLoading ? 'Processing…' : killSwitchActive ? 'Deactivate' : 'Activate Kill Switch'}
          </button>
        </div>

        {/* Autonomy Mode */}
        <div className="card" style={{ flex: '0 0 auto', minWidth: 200 }}>
          <div className="card-header">
            <span className="card-title">Autonomy Mode</span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {(['approval', 'full_autonomy'] as AutonomyMode[]).map((mode) => (
              <button
                key={mode}
                onClick={() => setAutonomyMode(mode)}
                style={{
                  flex: 1, padding: '8px 6px',
                  borderRadius: 'var(--radius-md)',
                  border: `1px solid ${autonomyMode === mode ? 'var(--accent, #6366f1)' : 'var(--border-primary)'}`,
                  background: autonomyMode === mode ? 'var(--accent-bg, rgba(99,102,241,0.1))' : 'var(--bg-surface)',
                  color: autonomyMode === mode ? 'var(--accent, #6366f1)' : 'var(--text-secondary)',
                  fontSize: 11, fontWeight: 600, cursor: 'pointer',
                }}
              >
                {mode === 'approval' ? '🛡️ Approval' : '🤖 Full Auto'}
              </button>
            ))}
          </div>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 6 }}>
            {autonomyMode === 'approval'
              ? 'Agents pause for human approval on critical actions'
              : 'Agents execute all actions autonomously'}
          </div>
        </div>

        {/* Daily Cost Summary */}
        <div className="card" style={{ flex: '1 1 200px', minWidth: 200 }}>
          <div className="card-header">
            <span className="card-title">Daily LLM Cost</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{ fontSize: 22, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
              ${totalDailyCost.toFixed(2)}
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              / ${totalBudget.toFixed(2)} budget
            </span>
          </div>
          {totalBudget > 0 && (
            <div style={{ marginTop: 6, height: 4, background: 'var(--bg-surface)', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${Math.min((totalDailyCost / totalBudget) * 100, 100)}%`,
                background: totalDailyCost / totalBudget > 0.8 ? 'var(--danger)' : 'var(--success)',
                borderRadius: 2,
                transition: 'width 0.3s',
              }} />
            </div>
          )}
        </div>
      </div>

      {/* Agent Cards Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12, marginBottom: 20 }}>
        {loading && agents.length === 0 && (
          <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: 40, color: 'var(--text-muted)', fontSize: 12 }}>
            Loading agents…
          </div>
        )}
        {agents.map((agent) => (
          <AgentCard
            key={agent.name}
            agent={agent}
            killSwitchActive={killSwitchActive}
            onToggle={(enabled) => toggleAgent(agent.name, enabled)}
            onStart={() => startAgent(agent.name)}
            onStop={() => stopAgent(agent.name)}
            onPause={() => pauseAgent(agent.name)}
            onResume={() => resumeAgent(agent.name)}
          />
        ))}
      </div>

      {/* Tabbed Sections */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--border-primary)' }}>
          {(['activity', 'pipelines', 'approvals', 'queue', 'costs'] as Tab[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: '10px 16px', fontSize: 12, fontWeight: 600,
                border: 'none', background: 'none', cursor: 'pointer',
                color: activeTab === tab ? 'var(--accent, #6366f1)' : 'var(--text-secondary)',
                borderBottom: activeTab === tab ? '2px solid var(--accent, #6366f1)' : '2px solid transparent',
                textTransform: 'capitalize',
              }}
            >
              {tab}
              {tab === 'approvals' && pendingApprovals.length > 0 && (
                <span className="badge badge-danger" style={{ marginLeft: 6, fontSize: 10 }}>
                  {pendingApprovals.length}
                </span>
              )}
            </button>
          ))}
        </div>

        <div style={{ padding: '12px 0', maxHeight: 400, overflowY: 'auto' }}>
          {activeTab === 'activity' && <ActivityTab events={activityEvents} />}
          {activeTab === 'pipelines' && <PipelinesTab pipelines={pipelines} onAbort={abortPipeline} />}
          {activeTab === 'approvals' && <ApprovalsTab approvals={pendingApprovals} onApprove={approveRequest} onDeny={denyRequest} />}
          {activeTab === 'queue' && <QueueTab queueDepth={queueDepth} />}
          {activeTab === 'costs' && <CostsTab llmUsage={llmUsage} />}
        </div>
      </div>

      {/* Equity Pause Section */}
      {equityPauseStatuses.length > 0 && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="card-header">
            <span className="card-title">Equity Curve Auto-Pause</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 10 }}>
            {equityPauseStatuses.map((ep) => (
              <EquityPauseCard key={ep.account_id} status={ep} onDeactivate={() => deactivateEquityPause(ep.account_id)} />
            ))}
          </div>
        </div>
      )}

      {/* Strategy Health Section */}
      {strategyHealth.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Strategy Health</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
            {strategyHealth.map((sh) => (
              <StrategyHealthCard key={sh.strategy_id} health={sh} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

// ---- Sub-Components ----

const AgentCard: FC<{
  agent: AgentInfo;
  killSwitchActive: boolean;
  onToggle: (enabled: boolean) => void;
  onStart: () => void;
  onStop: () => void;
  onPause: () => void;
  onResume: () => void;
}> = ({ agent, killSwitchActive, onToggle, onStart, onStop, onPause, onResume }) => {
  const isRunning = !['IDLE', 'FAILED', 'COMPLETED'].includes(agent.state);
  const canStart = agent.state === 'IDLE' && agent.enabled && !killSwitchActive;
  const canStop = isRunning;
  const canPause = ['PLANNING', 'EXECUTING'].includes(agent.state);
  const canResume = agent.state === 'PAUSED';

  return (
    <div className="card" style={{ opacity: agent.enabled ? 1 : 0.6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, textTransform: 'capitalize' }}>{agent.name}</div>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{agent.description}</div>
        </div>
        <span style={{
          padding: '2px 8px', borderRadius: 'var(--radius-sm)', fontSize: 10, fontWeight: 600,
          background: `${STATE_COLORS[agent.state]}20`,
          color: STATE_COLORS[agent.state],
        }}>
          {agent.state}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 12, fontSize: 10, color: 'var(--text-secondary)', marginBottom: 8 }}>
        <span>Uptime: {formatUptime(agent.uptime)}</span>
        <span>Errors: {agent.error_count}</span>
        <span>${agent.llm_cost_today_usd?.toFixed(2) ?? '0.00'}</span>
      </div>

      {agent.current_task_description && (
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8, fontStyle: 'italic' }}>
          {agent.current_task_description}
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <button onClick={onStart} disabled={!canStart} style={actionBtnStyle(canStart)}>Start</button>
        <button onClick={onStop} disabled={!canStop} style={actionBtnStyle(canStop)}>Stop</button>
        <button onClick={onPause} disabled={!canPause} style={actionBtnStyle(canPause)}>Pause</button>
        <button onClick={onResume} disabled={!canResume} style={actionBtnStyle(canResume)}>Resume</button>
        <div style={{ marginLeft: 'auto' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer', fontSize: 10 }}>
            <input
              type="checkbox"
              checked={agent.enabled}
              onChange={(e) => onToggle(e.target.checked)}
              style={{ accentColor: 'var(--accent, #6366f1)' }}
            />
            Enabled
          </label>
        </div>
      </div>
    </div>
  );
};

function actionBtnStyle(enabled: boolean): React.CSSProperties {
  return {
    padding: '4px 10px', fontSize: 10, fontWeight: 600,
    borderRadius: 'var(--radius-sm)',
    border: '1px solid var(--border-primary)',
    background: 'var(--bg-surface)',
    color: enabled ? 'var(--text-primary)' : 'var(--text-muted)',
    cursor: enabled ? 'pointer' : 'not-allowed',
    opacity: enabled ? 1 : 0.4,
  };
}

const ActivityTab: FC<{ events: { id: string; type: string; agent_name: string; message: string; timestamp: string }[] }> = ({ events }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
    {events.length === 0 && <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 11 }}>No recent activity</div>}
    {events.slice(0, 50).map((evt) => (
      <div key={evt.id} style={{
        display: 'flex', gap: 8, padding: '6px 12px', fontSize: 11,
        background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)',
      }}>
        <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', minWidth: 60, fontSize: 10 }}>
          {new Date(evt.timestamp).toLocaleTimeString()}
        </span>
        <span style={{ fontWeight: 600, minWidth: 80, textTransform: 'capitalize' }}>{evt.agent_name}</span>
        <span style={{ color: 'var(--text-secondary)', flex: 1 }}>{evt.message}</span>
      </div>
    ))}
  </div>
);

const PipelinesTab: FC<{ pipelines: { id: string; name: string; status: string; current_stage_index: number; stages: { agent_name: string; status: string }[]; created_at: string }[]; onAbort: (id: string) => void }> = ({ pipelines, onAbort }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
    {pipelines.length === 0 && <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 11 }}>No pipelines</div>}
    {pipelines.map((p) => (
      <div key={p.id} style={{ padding: '8px 12px', background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
          <span style={{ fontSize: 12, fontWeight: 600 }}>{p.name}</span>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span className={`badge ${p.status === 'RUNNING' ? 'badge-success' : p.status === 'FAILED' ? 'badge-danger' : ''}`} style={{ fontSize: 10 }}>
              {p.status}
            </span>
            {p.status === 'RUNNING' && (
              <button onClick={() => onAbort(p.id)} style={{ padding: '2px 8px', fontSize: 10, borderRadius: 'var(--radius-sm)', border: '1px solid var(--danger-border)', background: 'var(--danger-bg)', color: 'var(--danger)', cursor: 'pointer', fontWeight: 600 }}>
                Abort
              </button>
            )}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          {p.stages.map((_s, i) => (
            <div key={i} style={{
              flex: 1, height: 4, borderRadius: 2,
              background: i < p.current_stage_index ? 'var(--success)' : i === p.current_stage_index && p.status === 'RUNNING' ? 'var(--info, #3b82f6)' : 'var(--border-primary)',
            }} />
          ))}
        </div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 4 }}>
          Stage {p.current_stage_index + 1}/{p.stages.length} — {p.stages[p.current_stage_index]?.agent_name ?? 'done'}
        </div>
      </div>
    ))}
  </div>
);

const ApprovalsTab: FC<{
  approvals: { id: string; agent_name: string; action_description: string; created_at: string }[];
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
}> = ({ approvals, onApprove, onDeny }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
    {approvals.length === 0 && <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 11 }}>No pending approvals</div>}
    {approvals.map((a) => (
      <div key={a.id} style={{ padding: '10px 12px', background: 'var(--warning-bg, rgba(255,170,0,0.05))', borderRadius: 'var(--radius-sm)', border: '1px solid var(--warning-border, rgba(255,170,0,0.2))' }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{a.action_description}</div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8 }}>
          Requested by <strong>{a.agent_name}</strong> at {new Date(a.created_at).toLocaleString()}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={() => onApprove(a.id)} style={{ padding: '4px 14px', fontSize: 11, fontWeight: 600, borderRadius: 'var(--radius-sm)', border: 'none', background: 'var(--success)', color: '#fff', cursor: 'pointer' }}>
            Approve
          </button>
          <button onClick={() => onDeny(a.id)} style={{ padding: '4px 14px', fontSize: 11, fontWeight: 600, borderRadius: 'var(--radius-sm)', border: '1px solid var(--danger-border)', background: 'var(--danger-bg)', color: 'var(--danger)', cursor: 'pointer' }}>
            Deny
          </button>
        </div>
      </div>
    ))}
  </div>
);

const QueueTab: FC<{ queueDepth: { CRITICAL: number; HIGH: number; NORMAL: number; LOW: number } }> = ({ queueDepth }) => (
  <div style={{ display: 'flex', gap: 12, padding: '8px 12px' }}>
    {Object.entries(queueDepth).map(([priority, count]) => (
      <div key={priority} style={{ textAlign: 'center', flex: 1 }}>
        <div style={{ fontSize: 20, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{count}</div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'capitalize' }}>{priority.toLowerCase()}</div>
      </div>
    ))}
  </div>
);

const CostsTab: FC<{ llmUsage: { agent_name: string; daily_cost_usd: number; daily_tokens: number; budget_usd: number }[] }> = ({ llmUsage }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
    {llmUsage.length === 0 && <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 11 }}>No LLM usage data</div>}
    {llmUsage.map((u) => (
      <div key={u.agent_name} style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '6px 12px', background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)', fontSize: 11,
      }}>
        <span style={{ fontWeight: 600, textTransform: 'capitalize' }}>{u.agent_name}</span>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
          <span style={{ fontFamily: 'var(--font-mono)' }}>{u.daily_tokens.toLocaleString()} tokens</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>${u.daily_cost_usd.toFixed(2)}</span>
          <span style={{ color: 'var(--text-muted)' }}>/ ${u.budget_usd.toFixed(2)}</span>
        </div>
      </div>
    ))}
  </div>
);

// ---- Equity Pause Card (Task 33.4) ----

const EquityPauseCard: FC<{ status: EquityPauseStatus; onDeactivate: () => void }> = ({ status, onDeactivate }) => {
  const drawdownColor = status.paused
    ? 'var(--danger)'
    : status.current_drawdown_pct > status.max_drawdown_pct * 0.7
      ? 'var(--warning, #f59e0b)'
      : 'var(--success)';

  return (
    <div style={{
      padding: '10px 12px',
      background: status.paused ? 'var(--danger-bg)' : 'var(--bg-surface)',
      borderRadius: 'var(--radius-md)',
      border: `1px solid ${status.paused ? 'var(--danger-border)' : 'var(--border-primary)'}`,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>
          {status.account_label ?? `Account ${status.account_id.slice(0, 8)}`}
        </span>
        <span style={{
          padding: '2px 8px', borderRadius: 'var(--radius-sm)', fontSize: 10, fontWeight: 600,
          background: status.paused ? 'var(--danger)' : 'var(--success)',
          color: '#fff',
        }}>
          {status.paused ? 'PAUSED' : 'ACTIVE'}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 12, fontSize: 10, color: 'var(--text-secondary)', marginBottom: 6 }}>
        <div>
          <div style={{ color: 'var(--text-muted)' }}>Drawdown</div>
          <div style={{ fontWeight: 700, fontSize: 14, fontFamily: 'var(--font-mono)', color: drawdownColor }}>
            {status.current_drawdown_pct.toFixed(1)}%
          </div>
        </div>
        <div>
          <div style={{ color: 'var(--text-muted)' }}>HWM</div>
          <div style={{ fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
            ${status.high_water_mark.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
        </div>
        <div>
          <div style={{ color: 'var(--text-muted)' }}>Current</div>
          <div style={{ fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
            ${status.current_equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
        </div>
      </div>

      {/* Drawdown bar */}
      <div style={{ height: 4, background: 'var(--bg-secondary, #1a1a2e)', borderRadius: 2, overflow: 'hidden', marginBottom: 6 }}>
        <div style={{
          height: '100%',
          width: `${Math.min((status.current_drawdown_pct / status.max_drawdown_pct) * 100, 100)}%`,
          background: drawdownColor,
          borderRadius: 2,
          transition: 'width 0.3s',
        }} />
      </div>
      <div style={{ fontSize: 9, color: 'var(--text-muted)', marginBottom: 6 }}>
        Threshold: {status.max_drawdown_pct}% · Recovery: {status.recovery_pct}%
      </div>

      {status.paused && (
        <button
          onClick={onDeactivate}
          style={{
            width: '100%', padding: '6px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--warning-border, rgba(255,170,0,0.3))',
            background: 'var(--warning-bg, rgba(255,170,0,0.1))',
            color: 'var(--warning, #f59e0b)',
            fontSize: 10, fontWeight: 600, cursor: 'pointer',
          }}
        >
          Override — Deactivate Pause
        </button>
      )}
    </div>
  );
};

// ---- Strategy Health Card (Task 34.4) ----

const HEALTH_INDICATORS: Record<string, { color: string; label: string }> = {
  healthy: { color: 'var(--success)', label: '● Healthy' },
  warning: { color: 'var(--warning, #f59e0b)', label: '● Warning' },
  decaying: { color: 'var(--danger)', label: '● Decaying' },
  insufficient_data: { color: 'var(--text-muted)', label: '○ Insufficient Data' },
};

const StrategyHealthCard: FC<{ health: StrategyHealth }> = ({ health }) => {
  const indicator = HEALTH_INDICATORS[health.status] ?? HEALTH_INDICATORS.insufficient_data;

  const winRateDelta = health.rolling_win_rate != null && health.backtest_win_rate != null
    ? health.rolling_win_rate - health.backtest_win_rate
    : null;
  const pfDelta = health.rolling_profit_factor != null && health.backtest_profit_factor != null
    ? health.rolling_profit_factor - health.backtest_profit_factor
    : null;

  return (
    <div style={{
      padding: '10px 12px',
      background: 'var(--bg-surface)',
      borderRadius: 'var(--radius-md)',
      border: `1px solid ${health.status === 'decaying' ? 'var(--danger-border)' : health.status === 'warning' ? 'var(--warning-border, rgba(255,170,0,0.3))' : 'var(--border-primary)'}`,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>{health.strategy_name}</span>
        <span style={{ fontSize: 10, fontWeight: 600, color: indicator.color }}>{indicator.label}</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 10 }}>
        {/* Win Rate */}
        <div>
          <div style={{ color: 'var(--text-muted)', marginBottom: 2 }}>Win Rate</div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
              {health.rolling_win_rate != null ? `${(health.rolling_win_rate * 100).toFixed(1)}%` : '—'}
            </span>
            <span style={{ color: 'var(--text-muted)', fontSize: 9 }}>
              vs {health.backtest_win_rate != null ? `${(health.backtest_win_rate * 100).toFixed(1)}%` : '—'}
            </span>
            {winRateDelta != null && (
              <span style={{ fontSize: 9, fontWeight: 600, color: winRateDelta >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                {winRateDelta >= 0 ? '+' : ''}{(winRateDelta * 100).toFixed(1)}pp
              </span>
            )}
          </div>
        </div>

        {/* Profit Factor */}
        <div>
          <div style={{ color: 'var(--text-muted)', marginBottom: 2 }}>Profit Factor</div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
              {health.rolling_profit_factor != null ? health.rolling_profit_factor.toFixed(2) : '—'}
            </span>
            <span style={{ color: 'var(--text-muted)', fontSize: 9 }}>
              vs {health.backtest_profit_factor != null ? health.backtest_profit_factor.toFixed(2) : '—'}
            </span>
            {pfDelta != null && (
              <span style={{ fontSize: 9, fontWeight: 600, color: pfDelta >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                {pfDelta >= 0 ? '+' : ''}{pfDelta.toFixed(2)}
              </span>
            )}
          </div>
        </div>
      </div>

      <div style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 6 }}>
        {health.trade_count} / {health.lookback_trades} trades evaluated
      </div>
    </div>
  );
};

export default AgentsPage;
