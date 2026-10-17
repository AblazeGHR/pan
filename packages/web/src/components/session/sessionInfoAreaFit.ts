/**
 * Info-area (Session 卡片右下角) Job 标识 "J" 的宽度适配决策。
 *
 * 用户要求：宽度不足时 J 整枚消失（不是按 viewport 一律禁用），所有标识右对齐、
 * 数字徽标永远完整。纯 CSS 无法表达"内容是否放得下"——对内容尺寸的行容器做
 * container query 会在隐藏 J 后失去触发条件形成振荡，fixed-width 容器又恒不变，
 * 因此这里用一次同步布局测量做全部显/整隐决策：
 *
 * - 测量在 useLayoutEffect 中、于绘制前完成，隐藏不产生闪烁；
 * - 唯一的重测触发是改变兄弟宽度的 React props（队列数 / 未读数 / adapter），
 *   rail 宽度是固定 72px，无 ResizeObserver / timer / 轮询的需要；
 * - J 显隐只改 J 自身的存在，兄弟布局不受影响：隐藏时行宽恰好减少
 *   jWidth + gap（无残胶囊、无预留 gap），行的右缘（right 锚定）始终不动。
 */

/** 信息区底行与队列/未读徽标之间的 flex gap（CSS: .session-quick-action-adapter-row）。 */
export const INFO_AREA_GAP = 3;
/** 信息区底行相对 rail 右缘的内缩（CSS: right: 1px）。 */
export const INFO_AREA_RIGHT_INSET = 1;

export interface InfoAreaMeasurement {
  /** 行可占用的宽度（rail 宽度减去右内缩）。 */
  available: number;
  /** J 的自然宽度；J 当前隐藏时回落到缓存值（隐藏时 rect 为 0）。 */
  jWidth: number;
  /** 其余信息区标识（队列/未读/adapter）的自然宽度之和。 */
  othersWidth: number;
  /** 其余信息区标识的数量（J 存在时的 gap 数）。 */
  othersCount: number;
  /** 行的 flex gap（CSS: column-gap），显隐账目按实测值计算。 */
  gap: number;
}

function parseCssPixels(value: string | null | undefined, fallback: number): number {
  if (typeof value !== 'string') return fallback;
  const match = value.trim().match(/^(-?[\d.]+)px$/);
  if (!match) return fallback;
  const parsed = Number.parseFloat(match[1] ?? '');
  return Number.isFinite(parsed) ? parsed : fallback;
}

/**
 * 读取信息区底行的实时几何（分数像素）。`badge` 为 null 表示 J 当前未渲染
 * （隐藏态），此时 jWidth 取调用方缓存的自然宽度。
 */
export function measureInfoArea(
  row: HTMLElement,
  badge: HTMLElement | null,
  cachedJWidth: number,
): InfoAreaMeasurement {
  const rail = row.parentElement;
  const style = getComputedStyle(row);
  const rightInset = parseCssPixels(style.right, INFO_AREA_RIGHT_INSET);
  const gap = parseCssPixels(style.columnGap, INFO_AREA_GAP);
  const available = rail ? rail.clientWidth - rightInset : 0;
  const others = Array.from(row.children).filter((child) => child !== badge);
  const othersWidth = others.reduce(
    (sum, child) => sum + child.getBoundingClientRect().width,
    0,
  );
  const liveJWidth = badge ? badge.getBoundingClientRect().width : 0;
  return {
    available,
    jWidth: liveJWidth > 0 ? liveJWidth : cachedJWidth,
    othersWidth,
    othersCount: others.length,
    gap,
  };
}

/**
 * J 是否能与其他信息区标识并列放入底行。整枚显示或整枚隐藏由这一个布尔决定：
 * `jWidth + othersWidth + gap * othersCount <= available`（gap 取实测值，
 * 回落到 INFO_AREA_GAP 常量）。
 *
 * `available <= 0`（jsdom/SSR 等无真实布局的环境）时返回 true——无法测量就不
 * 误隐藏；真实浏览器里 rail 恒为 72px，该分支不会出现。
 */
export function jobBadgeFits(m: InfoAreaMeasurement): boolean {
  if (m.available <= 0) return true;
  const gap = m.gap > 0 ? m.gap : INFO_AREA_GAP;
  return m.jWidth + m.othersWidth + gap * m.othersCount <= m.available;
}
