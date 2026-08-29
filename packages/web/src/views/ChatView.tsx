import { useWebSocket } from '@/hooks/useWebSocket';
import { ChatLayout } from '@/components/layout/ChatLayout';
import { ChatMessages } from '@/components/chat/ChatMessages';
import { InputRow } from '@/components/chat/InputRow';
import { ApprovalBanner } from '@/components/chat/ApprovalBanner';

export default function ChatView() {
  // Initialize WebSocket connection and event routing
  useWebSocket();

  return (
    <ChatLayout>
      <div className="flex flex-col h-full min-h-0">
        <ApprovalBanner />
        <ChatMessages />
        <InputRow />
      </div>
    </ChatLayout>
  );
}
