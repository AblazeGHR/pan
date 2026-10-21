import type { SessionPredicate, SessionQueryField } from '@/types/actions';
export const queryFields: SessionQueryField[] = ['id', 'name', 'adapter', 'managedBy', 'readonlySession', 'pinned', 'workspaceIds'];
export const defaultQuery = (): SessionPredicate => ({ field: 'name', op: 'eq', value: 'Choose a Session name' });
export function queryOperators(field: SessionQueryField) {
  return field === 'workspaceIds' ? ['eq', 'ne', 'contains'] as const : field === 'name' ? ['eq', 'ne', 'in', 'contains'] as const : ['eq', 'ne', 'in'] as const;
}
export function defaultQueryValue(field: SessionQueryField, op: string): unknown {
  if (op === 'in') return [field === 'readonlySession' || field === 'pinned' ? false : ''];
  return field === 'readonlySession' || field === 'pinned' ? false : field === 'workspaceIds' && op !== 'contains' ? [] : '';
}
export function validateSessionQuery(value: unknown): string {
  let nodes = 0;
  const typed = (v: unknown, field: SessionQueryField): boolean => {
    if (field === 'readonlySession' || field === 'pinned') return typeof v === 'boolean';
    if (field === 'managedBy' && v === null) return true;
    if (field === 'workspaceIds') return Array.isArray(v) && v.length <= 128 && v.every((item) => typeof item === 'string' && [...item].length <= 512);
    return typeof v === 'string' && [...v].length <= 512;
  };
  function visit(raw: unknown, depth: number): string {
    if (++nodes > 64 || depth > 8) return 'Session query exceeds depth/node limit (8/64).';
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return 'Each query predicate must be an object.';
    const node = raw as Record<string, unknown>; const keys = Object.keys(node);
    if (keys.length === 1 && ('and' in node || 'or' in node)) {
      const children = node.and ?? node.or;
      if (!Array.isArray(children) || !children.length || children.length > 64) return 'AND/OR requires 1–64 predicates.';
      for (const child of children) { const error = visit(child, depth + 1); if (error) return error; } return '';
    }
    if (keys.length === 1 && 'not' in node) return visit(node.not, depth + 1);
    if (keys.length !== 3 || !keys.every((k) => ['field', 'op', 'value'].includes(k)) || !queryFields.includes(node.field as SessionQueryField)) return 'Unknown query field or predicate keys.';
    const field = node.field as SessionQueryField; const op = node.op; const v = node.value;
    if ((op === 'eq' || op === 'ne') && typed(v, field)) return '';
    if (op === 'in' && field !== 'workspaceIds' && Array.isArray(v) && v.length > 0 && v.length <= 128 && v.every((item) => typed(item, field))) return '';
    if (op === 'contains' && (field === 'name' || field === 'workspaceIds') && typeof v === 'string' && [...v].length <= 512 && (field !== 'name' || v.length > 0)) return '';
    return `Invalid operator or typed value for ${field}.`;
  }
  return visit(value, 1);
}
