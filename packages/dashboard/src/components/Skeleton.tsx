import { type FC, type CSSProperties } from 'react';

/**
 * Lightweight skeleton placeholder used during initial loads.
 *
 * Renders a subtly animated grey bar. Composes into larger shapes (rows,
 * tiles, tables) by passing width / height / borderRadius via props.
 *
 * Used in: MultiAccountSummary (first-load tiles), ControlTower (right rail).
 * Single source so all skeletons across the app share the same shimmer.
 */
interface SkeletonProps {
  width?: number | string;
  height?: number | string;
  borderRadius?: number | string;
  style?: CSSProperties;
}

const Skeleton: FC<SkeletonProps> = ({
  width = '100%',
  height = 14,
  borderRadius = 4,
  style,
}) => (
  <div
    aria-hidden="true"
    className="ui-skeleton"
    style={{
      width,
      height,
      borderRadius,
      background: 'var(--bg-surface)',
      backgroundImage:
        'linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.06) 50%, transparent 100%)',
      backgroundSize: '200% 100%',
      animation: 'ui-skeleton-shimmer 1.4s ease-in-out infinite',
      ...style,
    }}
  />
);

export default Skeleton;
