import { type FC } from 'react';
import AutopilotStatusCard from './AutopilotStatusCard';
import StrategyOverlayPanel from './StrategyOverlayPanel';
import SignalsPanel from './SignalsPanel';
import KillSwitchPanel from './KillSwitchPanel';

interface InfoPaneProps {
  activeView: string;
  instrument: string;
}

const InfoPane: FC<InfoPaneProps> = ({ activeView: _activeView, instrument }) => {
  return (
    <div className="info-pane-content">
      <div className="info-column">
        <AutopilotStatusCard />
        <StrategyOverlayPanel />
      </div>
      <div className="info-column">
        <SignalsPanel instrument={instrument} />
      </div>
      <div className="info-column">
        <KillSwitchPanel />
      </div>
    </div>
  );
};

export default InfoPane;
