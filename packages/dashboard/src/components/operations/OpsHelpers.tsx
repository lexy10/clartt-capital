/**
 * Shared visual primitives + tiny formatters used by the operational status
 * cards (Kill Switch, Autopilot, Health, Reconciliation). Extracted so the
 * right-rail (ControlTower) and the page bodies can share the same look.
 */
import { type FC } from 'react';
import { Link } from 'react-router-dom';

export type Tone = 'success' | 'warning' | 'danger' | 'muted';

export const StatusBadge: FC<{ tone: Tone; label: string; pulse?: boolean }> = ({ tone, label, pulse }) => (
  <span className={`live-desk-badge live-desk-badge-${tone}${pulse ? ' live-desk-blink' : ''}`}>{label}</span>
);

export const DetailLink: FC<{ to: string; children: string }> = ({ to, children }) => (
  <Link to={to} className="live-desk-link">{children}</Link>
);

export function timeAgo(iso?: string | null): string {
  if (!iso) return '—';
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return '—';
  const seconds = Math.floor((Date.now() - ts) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
