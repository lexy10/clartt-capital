import { type FC, type ReactNode } from 'react';

interface LayoutProps {
  sidebar: ReactNode;
  topBar: ReactNode;
  chartPane: ReactNode;
  infoPane: ReactNode;
  rightPanel: ReactNode;
  isChartFullscreen: boolean;
}

const Layout: FC<LayoutProps> = ({ sidebar, topBar, chartPane, infoPane, rightPanel, isChartFullscreen }) => {
  return (
    <div className="layout">
      <aside className="layout-sidebar">{sidebar}</aside>
      <div className="layout-center">
        {topBar}
        <div className={`split-pane${isChartFullscreen ? ' chart-fullscreen' : ''}`}>
          <div className="chart-pane">{chartPane}</div>
          <div className="info-pane">{infoPane}</div>
        </div>
      </div>
      <aside className="layout-right-panel">{rightPanel}</aside>
    </div>
  );
};

export default Layout;
