import { ChatLayout } from '@/components/layout/ChatLayout';
import { ChatMessages, type ChatMessagesHandle } from '@/components/chat/ChatMessages';
import { MessageNavigationRail } from '@/components/chat/MessageNavigationRail';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useRef } from 'react';
import { InputRow } from '@/components/chat/InputRow';
import { ApprovalBanner } from '@/components/chat/ApprovalBanner';
import { UserInputBanner } from '@/components/chat/UserInputBanner';
import { ElicitationBanner } from '@/components/chat/ElicitationBanner';
import { TerminalInteractionBanner } from '@/components/chat/TerminalInteractionBanner';

export default function ChatView() {
  const chatRef = useRef<ChatMessagesHandle>(null);
  // Unmounting (rather than hiding) the rail is the point of the switch: the
  // rail indexes the whole session history while it is mounted.
  const showMessageNavigationRail = useAppSettingsStore((s) => s.showMessageNavigationRail);

  return (
    <ChatLayout>
      <div className="flex flex-col h-full min-h-0">
        <ApprovalBanner />
        <UserInputBanner />
        <ElicitationBanner />
        <TerminalInteractionBanner />
        <div className="flex flex-1 min-h-0 min-w-0">
          <ChatMessages ref={chatRef} />
          {showMessageNavigationRail && <MessageNavigationRail chatRef={chatRef} />}
        </div>
        <InputRow />
      </div>
    </ChatLayout>
  );
}
