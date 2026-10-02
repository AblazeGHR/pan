import type { HistorySearchRole } from '@/types';

import { ALL_SEARCH_ROLES } from './searchRoleOptions';

export function HistorySearchRoles({ roles, onChange }: {
  roles: HistorySearchRole[];
  onChange: (roles: HistorySearchRole[]) => void;
}) {
  return (
    <fieldset className="history-search-roles" aria-label="Search content types">
      <legend className="sr-only">Search content types</legend>
      {ALL_SEARCH_ROLES.map((role) => (
        <label key={role}>
          <input type="checkbox" checked={roles.includes(role)}
            aria-label={`Search ${role} messages`}
            onChange={(event) => onChange(ALL_SEARCH_ROLES.filter((candidate) =>
              candidate === role ? event.target.checked : roles.includes(candidate)))} />
          {role}
        </label>
      ))}
    </fieldset>
  );
}
