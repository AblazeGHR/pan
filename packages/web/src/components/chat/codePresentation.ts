// Only the structural fields used here; do not add a dependency on HAST types.
interface HtmlNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HtmlNode[];
}

export const CODE_WINDOW_LINE_LIMIT = 200;
export const CODE_HIGHLIGHT_CHARACTER_LIMIT = 16_000;

export function needsCodeWindow(text: string): boolean {
  if (text.length > CODE_HIGHLIGHT_CHARACTER_LIMIT) return true;
  let lines = 1;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '\n' && ++lines > CODE_WINDOW_LINE_LIMIT) return true;
  }
  return false;
}

// Run BEFORE rehype-highlight: bounding DOM after highlighting still leaves
// the expensive full-payload syntax parser on the browser's main thread.
export function rehypeBoundCodeHighlight() {
  return (tree: HtmlNode) => {
    function visit(node: HtmlNode) {
      if (node.type === 'element' && node.tagName === 'pre') {
        for (const child of node.children ?? []) {
          if (child.type !== 'element' || child.tagName !== 'code') continue;
          const text = (child.children ?? []).map(part => part.type === 'text' ? part.value : '').join('');
          if (!needsCodeWindow(text.replace(/\n$/, ''))) continue;
          const properties = child.properties ?? (child.properties = {});
          const classes = properties.className;
          properties.className = [
            ...(Array.isArray(classes) ? classes : typeof classes === 'string' ? classes.split(/\s+/) : []),
            'no-highlight',
          ];
        }
      }
      for (const child of node.children ?? []) {
        if (child.type === 'element') visit(child);
      }
    }
    visit(tree);
  };
}

export function diffLineClass(line: string): string {
  if (line.startsWith('+') && !line.startsWith('+++')) return 'bg-green-500/10 border-l-2 border-green-500 pl-2 -ml-2';
  if (line.startsWith('-') && !line.startsWith('---')) return 'bg-red-500/10 border-l-2 border-red-500 pl-2 -ml-2';
  return '';
}
