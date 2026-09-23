// At this size Markdown parsing and pretty-printing can make a collapsed row
// noticeably expensive. Short blocks keep their existing eager behavior.
export const LONG_BLOCK_CONTENT_THRESHOLD = 24_000;

export function isLongBlockContent(content: string): boolean {
  return content.length > LONG_BLOCK_CONTENT_THRESHOLD;
}
