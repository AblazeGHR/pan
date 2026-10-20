/**
 * 模型显示标签（额度消耗倍率）。
 *
 * 在模型名后追加居右的 `(x0.08)` 形式倍率后缀，仅用于显示——提交与持久化
 * 始终使用原始模型 ID（见 ModelSelect 及各选择入口的 value/onChange）。
 *
 * 规则：
 * - 无倍率表、模型不在表中、值非有限数或为负 → 原样返回模型名（不默认 x1）；
 * - 0 是有效值（免费模型）→ 显示 `(x0)`；
 * - 小数按 JS 数字默认字符串化（0.08 → "0.08"，2 → "2"，0.8 → "0.8"）；
 * - 对已以同值后缀结尾的输入幂等，避免重复追加。
 */

/** 倍率后缀（如 ` (x0.08)`）；无有效倍率时返回空串。 */
export function modelMultiplierSuffix(
  model: string,
  multipliers?: Record<string, number> | null,
): string {
  if (!model) return '';
  const value = multipliers?.[model];
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '';
  const suffix = ` (x${value})`;
  if (model.endsWith(suffix)) return '';
  return suffix;
}

/** 模型显示标签：模型名 + 倍率后缀（无有效倍率时即模型名本身）。 */
export function formatModelLabel(
  model: string,
  multipliers?: Record<string, number> | null,
): string {
  if (!model) return model;
  return `${model}${modelMultiplierSuffix(model, multipliers)}`;
}
