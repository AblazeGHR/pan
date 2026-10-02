import { createContext } from 'react';

// Scope highlighting to the selected mounted message. React owns every mark;
// closing search restores the ordinary rendering without DOM mutation.
export const SearchTextContext = createContext({ query: '', occurrence: 0 });
type SearchNode = { type: string; value?: string; tagName?: string; children?: SearchNode[]; properties?: Record<string, unknown> };
export function searchTextPlugin(query: string, activeOccurrence = 0) {
  const needle = query.toLowerCase();
  return () => (tree: SearchNode) => {
    let occurrence = 0;
    const visit = (parent: SearchNode) => {
      if (!parent.children || parent.tagName === 'mark') return;
      parent.children = parent.children.flatMap((node) => {
        if (node.type !== 'text' || !node.value || !needle) { visit(node); return [node]; }
        // Do not use offsets from a length-changing Unicode lowercase mapping.
        const folded = node.value.toLowerCase();
        if (folded.length !== node.value.length) return [node];
        const parts: SearchNode[] = [];
        let start = 0, position = folded.indexOf(needle);
        while (position >= 0) {
          if (position > start) parts.push({ type: 'text', value: node.value.slice(start, position) });
          const active = occurrence === activeOccurrence;
          if (occurrence < 500 || active) {
            parts.push({ type: 'element', tagName: 'mark', properties: { className: active ? ['history-search-word', 'history-search-word-active'] : ['history-search-word'] },
              children: [{ type: 'text', value: node.value.slice(position, position+needle.length) }] });
          } else parts.push({ type: 'text', value: node.value.slice(position, position+needle.length) });
          occurrence += 1;
          start = position+needle.length;
          position = folded.indexOf(needle, start);
        }
        if (!parts.length) return [node];
        if (start < node.value.length) parts.push({ type: 'text', value: node.value.slice(start) });
        return parts;
      });
    };
    visit(tree);
  };
}
