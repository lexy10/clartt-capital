import { type FC } from 'react';
import type { Signal, SignalExecutionStatus } from '../types/signal';
import { signalExecution } from '../types/signal';

/** Colour per execution status. executed=good, no_fill=bad (a live signal that
 *  should have traded but didn't), paper/backtest/pending=neutral-ish. */
const TONE: Record<SignalExecutionStatus, { fg: string; bg: string; bd: string }> = {
  executed: { fg: 'var(--success)', bg: 'var(--success-bg)', bd: 'var(--success-border)' },
  no_fill: { fg: 'var(--danger)', bg: 'var(--danger-bg)', bd: 'var(--danger-border)' },
  paper: { fg: 'var(--warning)', bg: 'var(--warning-bg)', bd: 'var(--warning)' },
  backtest: { fg: 'var(--text-muted)', bg: 'transparent', bd: 'var(--glass-border)' },
  pending: { fg: 'var(--text-secondary)', bg: 'transparent', bd: 'var(--glass-border)' },
};

/**
 * Badge answering "did this signal trade, and if not, why?". Hover shows the
 * full reason. Falls back to a mode-based guess when the backend hasn't
 * attached execution info (e.g. a just-arrived live WS signal).
 */
const SignalStatusBadge: FC<{ signal: Signal }> = ({ signal }) => {
  const exec = signalExecution(signal);
  const tone = TONE[exec.status];
  return (
    <span
      title={exec.reason}
      style={{
        display: 'inline-block',
        padding: '2px 7px',
        borderRadius: 'var(--radius-sm)',
        fontSize: 10,
        fontWeight: 600,
        whiteSpace: 'nowrap',
        color: tone.fg,
        background: tone.bg,
        border: `1px solid ${tone.bd}`,
        cursor: 'help',
      }}
    >
      {exec.label}
    </span>
  );
};

export default SignalStatusBadge;
