import { type FC } from 'react';
import { useHealthStore } from '../stores/healthStore';

const CircuitBreakerWarningBanner: FC = () => {
  const { warningBannerVisible, circuitBreakers } = useHealthStore();

  if (!warningBannerVisible) return null;

  const openBreakers = circuitBreakers.filter((cb) => cb.state === 'open');

  return (
    <div
      role="alert"
      style={{
        padding: '12px 16px',
        backgroundColor: '#fef2f2',
        borderLeft: '4px solid #ef4444',
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        fontSize: '14px',
      }}
    >
      <span style={{ color: '#ef4444', fontWeight: 600 }}>Circuit Breaker Open</span>
      <span>
        {openBreakers.map((cb) => cb.protectedDependency).join(', ')}
        {openBreakers.length === 1 ? ' is' : ' are'} unavailable
      </span>
    </div>
  );
};

export default CircuitBreakerWarningBanner;
