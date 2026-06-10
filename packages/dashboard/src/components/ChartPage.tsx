import ChartArea from './ChartArea';
import { useChartStore } from '../stores/chartStore';

export default function ChartPage() {
  const isFullscreen = useChartStore((s) => s.isFullscreen);
  const toggleFullscreen = useChartStore((s) => s.toggleFullscreen);

  return <ChartArea isFullscreen={isFullscreen} onToggleFullscreen={toggleFullscreen} />;
}
