/**
 * Session 默认命名工具：计算最小可用序号。
 *
 * 默认名应为 `session-<n>`，n 取「最小缺失的正整数」——删除/改名后释放的
 * 序号应被复用，而不是随 session 总数单调上涨（此前用 `sessions.length + 1`
 * 导致序号只增不减，且与自定义名混淆时完全错位）。
 *
 * 只扫描形如 `session-<数字>` 的名字；自定义名（如 `code-review`）不参与
 * 计数，也不占用序号。
 */

export function nextSessionDefaultName(
  sessions: { name: string }[],
): string {
  const used = new Set<number>();
  for (const s of sessions) {
    const m = /^session-(\d+)$/.exec(s.name.trim());
    if (m) used.add(Number(m[1]));
  }
  let n = 1;
  while (used.has(n)) n += 1;
  return `session-${n}`;
}
