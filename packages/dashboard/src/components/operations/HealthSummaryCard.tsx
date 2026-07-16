import { type FC } from 'react';
import { useHealthStore } from '../../stores/healthStore';
import { ROUTES } from '../../types/api';
import { StatusBadge, DetailLink, timeAgo, type Tone } from './OpsHelpers';

/** Right-rail compact card summarizing services / breakers / consumer-lag.
 *  Drives an at-a-glance "is the engine up?" reading without diving into the
 *  full Health page. */
const HealthSummaryCard: FC = () => {
  const services = useHealthStore((s) => s.services);
  const circuitBreakers = useHealthStore((s) => s.circuitBreakers);
  const consumerLags = useHealthStore((s) => s.consumerLags);
  const lastRefresh = useHealthStore((s) => s.lastRefresh);

  const healthyCount = services.filter((service) => service.status === 'healthy').length;
  const unhealthyCount = services.filter((service) => service.status === 'unhealthy').length;
  const openBreakers = circuitBreakers.filter((breaker) => breaker.state === 'open');
  const lagAlerts = consumerLags.filter((lag) => lag.lag > lag.threshold);
  const tone: Tone = unhealthyCount > 0 || openBreakers.length > 0
    ? 'danger'
    : lagAlerts.length > 0
      ? 'warning'
      : 'success';
  // At least one hard error (down service or open breaker) → make the whole
  // card + badge pulse red so it's impossible to miss at a glance.
  const alert = tone === 'danger';

  return (
    <div className={`live-desk-card live-desk-card-compact${alert ? ' live-desk-card-alert' : ''}`}>
      <div className="live-desk-card-header">
        <span>System</span>
        <StatusBadge tone={tone} label={tone === 'success' ? 'Clear' : 'Check'} pulse={alert} />
      </div>
      <div className="live-desk-pair-row">
        <span>Services</span>
        <strong>{healthyCount}/{services.length || 3}</strong>
      </div>
      <div className="live-desk-pair-row">
        <span>Open breakers</span>
        <strong className={openBreakers.length > 0 ? 'live-desk-text-danger' : ''}>
          {openBreakers.length}
        </strong>
      </div>
      <div className="live-desk-pair-row">
        <span>Stream lag alerts</span>
        <strong className={lagAlerts.length > 0 ? 'live-desk-text-warning' : ''}>
          {lagAlerts.length}
        </strong>
      </div>
      <div className="live-desk-card-footer">
        <span>Updated {timeAgo(lastRefresh)}</span>
        <DetailLink to={ROUTES.HEALTH}>Health</DetailLink>
      </div>
    </div>
  );
};

export default HealthSummaryCard;
