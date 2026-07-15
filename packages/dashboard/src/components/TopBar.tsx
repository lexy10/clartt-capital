import { type FC } from 'react';
import ConnectionStatus from './ConnectionStatus';
import AutopilotToggle from './AutopilotToggle';
import UserGreeting from './UserGreeting';
import UserSwitcher from './UserSwitcher';
import ThemeToggle from './ThemeToggle';

/**
 * Top bar shown above every page.
 *
 * Left:
 *   - Greeting / name (admins see "Welcome, X", traders see just their name)
 *   - Live connection status
 *
 * Right:
 *   - Admin-only user impersonation dropdown (hidden for traders)
 *   - Autopilot master toggle
 */
const TopBar: FC = () => {
  return (
    <div className="top-bar">
      <div className="top-bar-left">
        <UserGreeting />
        <ConnectionStatus />
      </div>
      <div className="top-bar-right">
        <UserSwitcher />
        <ThemeToggle />
        <AutopilotToggle />
      </div>
    </div>
  );
};

export default TopBar;
