import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { MarkdownRenderer } from '../../src/components/chat/MarkdownRenderer';
import { SearchTextContext } from '../../src/components/chat/searchText';
import '../../src/index.css';

const content = String.raw`Inline $x^2 + y^2 = z^2$ in an ordinary message.

$$
\int_0^1 x^2\,dx = \frac{1}{3}
$$

Wide equation:

$$
\underbrace{a+b+c+d+e+f+g+h+i+j+k+l+m+n+o+p+q+r+s+t+u+v+w+x+y+z}_{\text{scroll within this formula on a narrow screen}} = 0
$$

Invalid $\frac{$ but **the rest of this message stays visible**.

needle [web link](https://example.test) [attachment](/api/attachments/ref/att_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?session_id=owned)

Escaped currency: \$5 and \$10. Lone currency: $20.

Inline code: ` + '`$literal$`' + String.raw`

` + '```latex\n$x^2$\n```\n\n```math\nx^2\n```' + String.raw`

| Markdown | Status |
| - | - |
| ordinary table | preserved |
`;

function Harness() {
  const [stream, setStream] = useState('Streaming $\\frac{a}');
  const [search, setSearch] = useState(false);
  return <MemoryRouter><main style={{ maxWidth: 820, margin: '0 auto', padding: 20, minWidth: 0 }}>
    <h1>Chat KaTeX production verification</h1>
    <button onClick={() => setSearch(value => !value)}>Toggle search</button>{' '}
    <button onClick={() => setStream('Streaming $\\frac{a}{b}$ complete')}>Complete stream</button>
    <SearchTextContext.Provider value={{ query: search ? 'needle' : '', occurrence: 0 }}>
      <section data-testid="message"><MarkdownRenderer content={content} /></section>
      <section data-testid="stream"><MarkdownRenderer content={stream} /></section>
    </SearchTextContext.Provider>
  </main></MemoryRouter>;
}

createRoot(document.getElementById('root')!).render(<Harness />);
