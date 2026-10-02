import { useId, useState, type ReactNode } from 'react';
import type { ApiHistorySearchHit } from '@/types';

/** Render text nodes only; Unicode folding maps expanded letters to source spans. */
export function SearchSnippet({ text, query }: { text: string; query: string }) {
  const fold = (value: string) => value.toLowerCase().replace(/ß/g, 'ss').replace(/ς/g, 'σ');
  const needle = fold(query.trim());
  if (!needle) return <>{text}</>;
  let folded = '';
  const spans: { start: number; end: number }[] = [];
  let offset = 0;
  for (const character of text) {
    const part = fold(character);
    folded += part;
    for (let i = 0; i < part.length; i++) spans.push({ start: offset, end: offset + character.length });
    offset += character.length;
  }
  const ranges: { start: number; end: number }[] = [];
  for (let position = folded.indexOf(needle); position >= 0; position = folded.indexOf(needle, position + needle.length)) {
    const first = spans[position], last = spans[position + needle.length - 1];
    if (!first || !last) break;
    const start = first.start, end = last.end;
    const previous = ranges.at(-1);
    if (previous && previous.end > start) previous.end = Math.max(previous.end, end);
    else ranges.push({ start, end });
  }
  const nodes = [];
  let cursor = 0;
  for (const range of ranges) {
    nodes.push(text.slice(cursor, range.start), <mark className="history-search-word" key={range.start}>{text.slice(range.start, range.end)}</mark>);
    cursor = range.end;
  }
  nodes.push(text.slice(cursor));
  return <>{nodes}</>;
}

export function HistorySearchOverview({ hits, query, sessionName, onSelect, disabled = false, children }: {
  hits: ApiHistorySearchHit[]; query: string; sessionName?: (id: string) => string;
  onSelect: (hit: ApiHistorySearchHit) => void; disabled?: boolean; children?: ReactNode;
}) {
  const [expanded, setExpanded] = useState(true);
  const id = useId();
  return <>
    <button type="button" className="history-search-overview-toggle" aria-expanded={expanded} aria-controls={id}
      onClick={() => setExpanded((value) => !value)}>{expanded ? 'Collapse results' : 'Expand results'}</button>
    <div id={id} hidden={!expanded} className="history-search-overview">
      <div className="global-history-search__results" role="list" aria-label={sessionName ? 'Global history results' : 'Session history results'}>
        {hits.map((hit) => <div key={`${hit.sessionId}:${hit.messageId}`} className="global-history-search__result-row" role="listitem">
          <button type="button" className="global-history-search__result" disabled={disabled}
            aria-label={`${sessionName ? `${sessionName(hit.sessionId)}, ` : ''}${hit.role}, ${hit.snippet}`} onClick={() => onSelect(hit)}>
            <span className="global-history-search__result-meta">
              {sessionName && <span className="global-history-search__session-name">{sessionName(hit.sessionId)}</span>}
              <span className="global-history-search__role">{hit.role}</span><span>{hit.matchCount ?? 1} occurrences</span>
            </span>
            <span className="global-history-search__snippet"><SearchSnippet text={hit.snippet} query={query} /></span>
          </button>
        </div>)}
      </div>
      {children}
    </div>
  </>;
}
