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
    <div className="absolute bottom-0 left-0 right-0 px-3 md:px-5 pt-4 md:pt-5 pb-[calc(env(safe-area-inset-bottom)+12px)] md:pb-5 bg-gradient-to-t from-[#f6f1ea]/85 via-[#f6f1ea]/40 to-transparent pointer-events-none">
      <div className="max-w-4xl mx-auto space-y-2.5 md:space-y-3 pointer-events-auto">
        {showQuickActions && (
          <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-none">
            {QUICK_ACTIONS.map((action) => (
              <button
                key={action.label}
                onClick={() => setMessage(action.query)}
                className="px-4 py-1.5 rounded-full bg-white/85 border border-slate-200/80 text-xs font-semibold text-slate-700 hover:text-slate-900 hover:border-slate-300 hover:bg-white transition-all whitespace-nowrap shadow-sm"
              >
                {action.label}
              </button>
            ))}
          </div>
        )}

        <form
          onSubmit={handleSubmit}
          className="flex items-end bg-white border border-slate-200/80 rounded-3xl py-2 pl-3 pr-2 shadow-lg shadow-slate-900/5 backdrop-blur-md transition-all focus-within:border-slate-300 focus-within:shadow-slate-900/10"
        >
          <button
            type="button"
            tabIndex={-1}
            className="p-2 text-slate-500 hover:text-slate-800 bg-slate-100/60 hover:bg-slate-100 rounded-full transition-all focus:outline-none cursor-pointer self-end mb-1"
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
            className="flex-1 bg-transparent text-slate-900 placeholder:text-slate-400 px-3 py-2 focus:outline-none resize-none min-h-[40px] max-h-40 text-sm font-medium leading-relaxed"
          />

          <div className="flex items-center gap-1 self-end mb-0.5">
            <VoiceInput disabled={disabled} />
            <button
              type="submit"
              disabled={!message.trim() || disabled}
              aria-label="Send message"
              className="w-9 h-9 bg-slate-950 hover:bg-slate-800 disabled:opacity-40 text-white rounded-full flex items-center justify-center transition-all duration-200 hover:scale-105 active:scale-95 cursor-pointer shadow-md shadow-slate-900/10"
            >
              <Send size={15} className="-ml-0.5" />
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
