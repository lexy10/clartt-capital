import { type FC, type ReactNode } from 'react';
import { useAutopilotStore } from '../stores/autopilotStore';

/**
 * Hook that returns whether manual trade controls should be disabled.
 * Use this in any component that allows manual trade placement
 * (buy/sell buttons, order forms, position sizing inputs, etc.)
 * to check if autopilot is active and block user interaction.
 */
export function useAutopilotGuard(): {
  manualTradingDisabled: boolean;
  reason: string;
} {
  const enabled = useAutopilotStore((s) => s.enabled);
  return {
    manualTradingDisabled: enabled,
    reason: enabled
      ? 'Manual trading disabled while autopilot is active'
      : '',
  };
}

/* ── Styles ─────────────────────────────────────────────── */

const guardWrapperStyle = (disabled: boolean): React.CSSProperties => ({
  position: 'relative',
  opacity: disabled ? 0.45 : 1,
  pointerEvents: disabled ? 'none' : 'auto',
  transition: 'opacity 250ms ease',
  userSelect: disabled ? 'none' : 'auto',
});

const bannerStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
  padding: '8px 12px',
  marginBottom: '8px',
  background: 'rgba(88, 166, 255, 0.08)',
  border: '1px solid rgba(88, 166, 255, 0.25)',
  borderRadius: '8px',
  fontSize: '12px',
  fontWeight: 500,
  color: 'var(--accent, #58a6ff)',
  fontFamily: "'Inter', var(--font-sans)",
};

const iconStyle: React.CSSProperties = {
  flexShrink: 0,
  width: '16px',
  height: '16px',
};

/* ── Component ──────────────────────────────────────────── */

interface TradeControlGuardProps {
  /** Content to render (trade controls, order forms, etc.) */
  children: ReactNode;
  /** Optional custom message when autopilot is active */
  message?: string;
}

/**
 * Wraps manual trade placement controls and disables them when autopilot
 * is active. Shows an informational banner explaining why controls are
 * disabled. Future trade components (buy/sell panels, order forms) should
 * be wrapped with this component.
 *
 * @example
 * ```tsx
 * <TradeControlGuard>
 *   <BuySellPanel />
 * </TradeControlGuard>
 * ```
 */
const TradeControlGuard: FC<TradeControlGuardProps> = ({
  children,
  message = 'Manual trading disabled while autopilot is active',
}) => {
  const { manualTradingDisabled } = useAutopilotGuard();

  return (
    <div>
      {manualTradingDisabled && (
        <div style={bannerStyle} role="status" aria-live="polite">
          <svg
            style={iconStyle}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
          <span>{message}</span>
        </div>
      )}
      <div
        style={guardWrapperStyle(manualTradingDisabled)}
        aria-disabled={manualTradingDisabled}
      >
        {children}
      </div>
    </div>
  );
};

export default TradeControlGuard;
