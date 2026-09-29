import { createRoot } from 'react-dom/client';
import '../src/index.css';
import type { Message } from '../src/types';
import { NonBodyGroup } from '../src/components/chat/NonBodyGroup';
import { groupChildren } from '../src/components/chat/nonBodyGroupUtils';

declare global {
  interface Window {
    __nonBodyVirtualization?: {
      messageCount: number;
      childGroupCount: number;
    };
  }
}

const items: Message[] = Array.from({ length: 240 }, (_, index) => ({
  role: index % 2 === 0 ? 'thinking' : 'tool',
  content: index % 2 === 0
    ? `A short streamed thought for child ${index}`
    : `tool call: Bash\nargs: {"command":"echo child-${index}"}`,
  blockId: `browser-non-body-${index}`,
}));

window.__nonBodyVirtualization = {
  messageCount: items.length,
  childGroupCount: groupChildren(items).length,
};

createRoot(document.getElementById('root')!).render(<NonBodyGroup items={items} />);
