import type { HistorySearchRole } from '@/types';
export const ALL_SEARCH_ROLES: HistorySearchRole[] = ['user', 'assistant', 'tool', 'thinking'];
/**
 * Initial selection for a fresh search UI. Tool and thinking stay opt-in so the
 * default result set matches the body content users expect, while every role in
 * ALL_SEARCH_ROLES remains manually selectable.
 */
export const DEFAULT_SEARCH_ROLES: HistorySearchRole[] = ['user', 'assistant'];
