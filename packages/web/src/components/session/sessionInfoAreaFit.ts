/**
 * Info-area (Session 卡片右下角) Job 标识 "J" 的宽度适配决策。
 *
 * 用户要求：宽度不足时 J 整枚消失（不是按 viewport 一律禁用），所有标识右对齐、
 * 数字徽标永远完整。纯 CSS 无法表达"内容是否放得下"——对内容尺寸的行容器做
 * container query 会在隐藏 J 后失去触发条件形成振荡，fixed-width 容器又恒定，
 * 因此这里用一次同步布局测量做全部显/整隐决策：
 *
 * - 测量在 useLayoutEffect 中、于绘制前完成，隐藏不产生闪烁；
 * - 唯一的重测触发是改变兄弟宽度的 React props（队列数 / 未读数 / adapter）
 *   plus 一次性的 document.fonts.ready（webfont swap 会改变字形宽度）；
 *   rail 宽度是固定 72px，无 ResizeObserver / timer / 轮询的需要；
 * - J 显隐只改 J 自身的存在，兄弟布局不受影响：隐藏时行宽恰好减少
 *   jWidth + gap（无残胶囊、无预留 gap），行的右缘（right 锚定）始终不动。
 *
 * J 的自然宽度取自常驻探针（`data-session-job-metric="probe"`，与徽标同类、
 * 绝对定位 + visibility:hidden，不占布局不绘制），而非可见徽标本身——徽标
 * 隐藏时已卸载，只有探针能在任何字体状态下给出真实自然宽度，因此不存在
 * 缓存陈旧：字体到达后无论 J 当时是显示还是隐藏，重测都读到新字形的宽度。
 */

/** 标记信息区底行中参与 J 度量的元素（探针与可见徽标），others 求和时排除。 */
export const JOB_METRIC_ATTR = 'data-session-job-metric';

/** 信息区底行与队列/未读徽标之间的 flex gap（CSS: .session-quick-action-adapter-row）。 */
export const INFO_AREA_GAP = 3;
/** 信息区底行相对 rail 右缘的内缩（CSS: right: 1px）。 */
export const INFO_AREA_RIGHT_INSET = 1;

export interface InfoAreaMeasurement {
  /** 行可占用的宽度（rail 宽度减去右内缩，CSS px）。 */
  available: number;
  /** J 的自然宽度（探针实测；无探针时为 0）。 */
  jWidth: number;
  /** 其余信息区标识（队列/未读/adapter）的自然宽度之和。 */
  othersWidth: number;
  /** 其余信息区标识的数量（J 存在时的 gap 数）。 */
  othersCount: number;
  /** 行的 flex gap（CSS: column-gap），显隐账目按实测值计算。 */
  gap: number;
  /** rail 的渲染缩放因子（rect 宽 / clientWidth）；1 表示无缩放。 */
  zoom: number;
}

function parseCssPixels(value: string | null | undefined, fallback: number): number {
  if (typeof value !== 'string') return fallback;
  const match = value.trim().match(/^(-?[\d.]+)px$/);
  if (!match) return fallback;
  const parsed = Number.parseFloat(match[1] ?? '');
  return Number.isFinite(parsed) ? parsed : fallback;
}

/**
 * 读取信息区底行的实时几何（统一换算为 CSS px）。`jProbe` 为常驻探针元素
 * （J 隐藏时唯一可用的 J 宽度来源）；为 null 时 jWidth 记 0。
 *
 * 单位归一化依据（实测）：`getBoundingClientRect()` 随 CSS zoom 缩放，而
 * `clientWidth` 与 computed style 长度是 CSS px。zoom 1.25 时 rail rect=90 而
 * clientWidth=72——若混用，拥挤判定会在缩放下失真。故以 rail 自身的
 * rect/clientWidth 为缩放因子，把元素 rect 折算回 CSS px，整个不等式同单位。
 */
export function measureInfoArea(
  row: HTMLElement,
  jProbe: HTMLElement | null,
): InfoAreaMeasurement {
  const rail = row.parentElement;
  const style = getComputedStyle(row);
  const rightInset = parseCssPixels(style.right, INFO_AREA_RIGHT_INSET);
  const gap = parseCssPixels(style.columnGap, INFO_AREA_GAP);
  const railClientWidth = rail ? rail.clientWidth : 0;
  const railRectWidth = rail ? rail.getBoundingClientRect().width : 0;
  const zoom = railClientWidth > 0 && railRectWidth > 0 ? railRectWidth / railClientWidth : 1;
  const toCssPx = (value: number) => (zoom > 0 ? value / zoom : value);
  const available = railClientWidth - rightInset;
  // 探针与可见徽标都带度量标记，不计入兄弟宽度。
  const others = Array.from(row.children).filter(
    (child) => !child.hasAttribute(JOB_METRIC_ATTR),
  );
  const othersWidth = others.reduce(
    (sum, child) => sum + toCssPx(child.getBoundingClientRect().width),
    0,
  );
  const jWidth = jProbe ? toCssPx(jProbe.getBoundingClientRect().width) : 0;
  return {
    available,
    jWidth,
    othersWidth,
    othersCount: others.length,
    gap,
    zoom,
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
