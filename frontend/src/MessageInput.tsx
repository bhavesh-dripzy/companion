import React, { useEffect, useRef, useState } from 'react';
import { Plus, Send } from 'lucide-react';
import { VoiceInput } from './VoiceInput';

interface MessageInputProps {
  onSendMessage: (message: string) => void;
  disabled: boolean;
  showQuickActions: boolean;
}

// Quick-action chips that map straight to natural-language prompts the
// existing pipeline already knows how to route. Kept identical to the
// previous skin so behaviour doesn't change — only the chrome did.
const QUICK_ACTIONS = [
  { label: 'Biryani', query: 'Find good biryani near my saved address' },
  { label: 'Pizza', query: 'Find pizza near my saved address' },
  { label: 'Groceries', query: 'Search milk and brown bread on Zepto' },
  { label: 'Today’s ETA', query: 'How long to reach office from here?' },
];

export const MessageInput: React.FC<MessageInputProps> = ({
  onSendMessage,
  disabled,
  showQuickActions,
}) => {
  const [message, setMessage] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (message.trim() && !disabled) {
      onSendMessage(message);
      setMessage('');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 160)}px`;
    }
  }, [message]);

  return (
    <div className="absolute bottom-0 left-0 right-0 px-3 md:px-5 pt-4 md:pt-5 pb-[calc(env(safe-area-inset-bottom)+12px)] md:pb-5 bg-gradient-to-t from-[#f6f3ee]/90 via-[#f6f3ee]/40 to-transparent pointer-events-none">
      <div className="max-w-4xl mx-auto space-y-2.5 md:space-y-3 pointer-events-auto">
        {showQuickActions && (
          <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-none">
            {QUICK_ACTIONS.map((action) => (
              <button
                key={action.label}
                onClick={() => setMessage(action.query)}
                className="px-4 py-1.5 rounded-full bg-white/80 backdrop-blur-sm border border-[#8b5cf6]/15 text-xs font-semibold text-[#6d6680] hover:text-[#1a1626] hover:border-[#8b5cf6]/40 hover:bg-[#8b5cf6]/8 transition-all whitespace-nowrap shadow-sm"
              >
                {action.label}
              </button>
            ))}
          </div>
        )}

        <form
          onSubmit={handleSubmit}
          className="spectrum-ring flex items-end bg-white/80 rounded-3xl py-2 pl-3 pr-2 shadow-lg shadow-[#3a5bef]/8 backdrop-blur-md transition-shadow duration-300 focus-within:shadow-[#8b5cf6]/12"
        >
          <button
            type="button"
            tabIndex={-1}
            className="p-2 text-[#6d6680] hover:text-[#8b5cf6] bg-[#8b5cf6]/8 hover:bg-[#8b5cf6]/14 rounded-full transition-all focus:outline-none cursor-pointer self-end mb-1"
            title="Attachment menu (coming soon)"
          >
            <Plus size={16} />
          </button>

          <textarea
            ref={textareaRef}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask Companion anything…"
            rows={1}
            disabled={disabled}
            className="flex-1 bg-transparent text-[#1a1626] placeholder:text-[#9a93a8] px-3 py-2 focus:outline-none resize-none min-h-[40px] max-h-40 text-sm font-medium leading-relaxed"
          />

          <div className="flex items-center gap-1 self-end mb-0.5">
            <VoiceInput disabled={disabled} />
            <button
              type="submit"
              disabled={!message.trim() || disabled}
              aria-label="Send message"
              className="btn-spectrum w-9 h-9 disabled:opacity-40 text-white rounded-full flex items-center justify-center hover:scale-105 active:scale-95 cursor-pointer"
            >
              <Send size={15} className="-ml-0.5" />
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
