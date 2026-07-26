import { useWebSocket } from '@/hooks/useWebSocket';
import { ChatLayout } from '@/components/layout/ChatLayout';
import { ChatMessages } from '@/components/chat/ChatMessages';
import { InputRow } from '@/components/chat/InputRow';

export default function ChatView() {
  // Initialize WebSocket connection and event routing
  useWebSocket();

  return (
    <ChatLayout>
      <div className="flex flex-col h-full">
        <ChatMessages />
        <InputRow />
      </div>
    </ChatLayout>
  );
}
