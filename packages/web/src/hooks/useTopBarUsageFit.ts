import { useLayoutEffect, useState, type RefObject } from 'react';

/**
 * Fit policy for the Chat TopBar usage labels.
 *
 * The bar is one row and must never wrap. When the real content (Session name,
 * Chat view controls, Terminal TUI entry, usage labels, worker controls) does
 * not fit, drop whole optional items in this order:
 *
 *   1. the context/token usage label ("12k tok / 200k")
 *   2. the cached account quota label ("quota 5h 20% / 周 45%")
 *
 * Widths come from the rendered elements, so the decision follows the real
 * available space — a long Session name or a wider/narrower Sidebar changes it
 * without any viewport breakpoint. The resolver is a pure function of the
 * measured widths, so the chosen level never feeds back into the inputs and
 * cannot oscillate; measurements only rerun on ResizeObserver events or when
 * the Session/labels change. No polling.
 */

/** Desktop cap from the name's `md:max-w-[200px]` class. */
export const NAME_MEASURE_CAP = 200;

/** Slack for sub-pixel text measurement; keeps the boundary stable. */
const FIT_TOLERANCE = 1;

export interface TopBarUsageFitInput {
  bar: RefObject<HTMLElement | null>;
  left: RefObject<HTMLElement | null>;
  name: RefObject<HTMLElement | null>;
  actions: RefObject<HTMLElement | null>;
  /** Optional labels in hiding priority order (context first, quota second). */
  labels: Array<RefObject<HTMLElement | null>>;
}

export interface UsageFitResolution {
  available: number;
  leftFixed: number;
  nameDesired: number;
  outerGap: number;
  otherActionsWidth: number;
  actionGap: number;
  /** Natural widths in the same order as the labels passed to the hook. */
  labelWidths: number[];
}

function px(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Smallest number of leading labels to hide so the row fits. 0 = show all.
 * `otherActionsWidth` excludes the optional labels (they are added back from
 * `labelWidths`), which keeps the result independent of the current level.
 */
export function resolveHiddenLabelCount(input: UsageFitResolution): number {
  const widths = input.labelWidths;
  const base =
    input.leftFixed + input.nameDesired + input.outerGap + input.otherActionsWidth;
  let required = base;
  for (const width of widths) required += width + input.actionGap;
  for (let hidden = 0; hidden <= widths.length; hidden += 1) {
    if (required <= input.available + FIT_TOLERANCE) return hidden;
    const width = widths[hidden];
    if (width !== undefined) required -= width + input.actionGap;
  }
  return widths.length;
}

function measure(
  refs: TopBarUsageFitInput,
  hidden: number,
): UsageFitResolution | null {
  const bar = refs.bar.current;
  const left = refs.left.current;
  const name = refs.name.current;
  const actions = refs.actions.current;
  if (!bar || !left || !name || !actions) return null;

  const barStyle = window.getComputedStyle(bar);
  const available = Math.max(
    0,
    bar.getBoundingClientRect().width - px(barStyle.paddingLeft) - px(barStyle.paddingRight),
  );
  const outerGap = px(barStyle.columnGap);
  const leftWidth = left.getBoundingClientRect().width;
  const nameWidth = name.getBoundingClientRect().width;
  const leftFixed = Math.max(0, leftWidth - nameWidth);
  // scrollWidth keeps the untruncated text width, so a long name is reserved
  // up to its 200px desktop cap instead of collapsing to what currently fits.
  const nameNatural = Math.max(name.scrollWidth, nameWidth);
  const nameDesired = Math.min(nameNatural, NAME_MEASURE_CAP);

  const actionsStyle = window.getComputedStyle(actions);
  const actionGap = px(actionsStyle.columnGap);
  const labelWidths: number[] = [];
  let visibleLabelWidth = 0;
  refs.labels.forEach((label, index) => {
    const element = label.current;
    if (!element) return;
    // Hidden labels stay mounted (absolutely positioned, invisible), so their
    // natural width is still measurable.
    const width = element.getBoundingClientRect().width
      + px(window.getComputedStyle(element).marginRight);
    labelWidths.push(width);
    if (index >= hidden) visibleLabelWidth += width + actionGap;
  });
  // Labels currently hidden are out of flow, so the actions box already
  // excludes them. Subtracting only the visible labels keeps this value
  // invariant across levels; the resolver adds back the hidden ones.
  const otherActionsWidth = Math.max(
    0,
    actions.getBoundingClientRect().width - visibleLabelWidth,
  );

  return {
    available,
    leftFixed,
    nameDesired,
    outerGap,
    otherActionsWidth,
    actionGap,
    labelWidths,
  };
}

/**
 * Returns how many leading optional labels must be hidden (0..labels.length).
 * `enabled` is false on mobile, where the labels are not rendered at all.
 * `revision` re-runs the measurement when the Session/label text changes.
 */
export function useTopBarUsageFit(
  refs: TopBarUsageFitInput,
  enabled: boolean,
  revision: string,
): number {
  const [hidden, setHidden] = useState(0);

  useLayoutEffect(() => {
    if (!enabled) {
      setHidden((current) => (current === 0 ? current : 0));
      return;
    }
    const update = () => {
      setHidden((current) => {
        const resolution = measure(refs, current);
        if (!resolution) return current;
        return resolveHiddenLabelCount(resolution);
      });
    };
    update();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(update);
    const observed = [refs.bar.current, refs.left.current, refs.name.current, refs.actions.current];
    for (const element of observed) if (element) observer.observe(element);
    return () => observer.disconnect();
  }, [enabled, revision, hidden, refs]);

  return hidden;
}
