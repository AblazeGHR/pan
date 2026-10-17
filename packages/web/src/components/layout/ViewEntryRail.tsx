import { NavLink } from 'react-router-dom';
import { entries } from './viewEntries';
import { ViewEntryBadge } from './ViewEntryBadge';

/**
 * Desktop collapsed nav rail (48px): one vertical icon entry per view
 * route, rendered inside the Sidebar's collapsed branch. Shares the
 * single entry list with the expanded ViewEntryGroups strip, so the
 * collapsed rail can never drift from the expanded entries. Mobile is
 * unaffected: the rail renders only when `!isMobile`.
 */
export function ViewEntryRail() {
  return (
    <>
      {entries.map((entry) => (
        <NavLink
          key={entry.to}
          to={entry.to}
          end={entry.end}
          title={entry.label}
          aria-label={entry.label}
          className={({ isActive }) =>
            `relative p-1.5 rounded transition-colors ${
              isActive
                ? 'text-accent bg-accent/10'
                : 'text-text-tertiary hover:text-text-primary hover:bg-bg-hover'
            }`
          }
        >
          <entry.icon size={18} />
          {entry.to === '/jobs' && <ViewEntryBadge />}
        </NavLink>
      ))}
    </>
  );
}
