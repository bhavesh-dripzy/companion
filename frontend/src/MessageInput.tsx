import React, { useState, useRef, useEffect } from 'react';
import { ArrowUp } from 'lucide-react';

interface MessageInputProps {
  onSendMessage: (message: string) => void;
  disabled: boolean;
}

export const MessageInput: React.FC<MessageInputProps> = ({
  onSendMessage,
  disabled,
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
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  }, [message]);

  const quickActions = [
    { label: 'Biryani', query: 'Find good biryani near my saved address' },
    { label: 'Pizza', query: 'Find pizza near my saved address' },
    { label: 'Groceries', query: 'Search milk and brown bread on Zepto' },
  ];

  return (
    <div className="p-4 md:p-6 bg-background/80 backdrop-blur-xl border-t border-border">
      <div className="max-w-4xl mx-auto space-y-3">
        {/* Quick Actions — soft pill chips above the composer. */}
        <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-none">
          {quickActions.map((action) => (
            <button
              key={action.label}
              onClick={() => setMessage(action.query)}
              className="px-4 py-1.5 rounded-full bg-surface border border-border text-xs font-semibold text-gray-300 hover:text-amber-300 hover:border-amber-500/40 hover:bg-amber-500/5 transition-all whitespace-nowrap"
            >
              {action.label}
            </button>
          ))}
        </div>

        {/* Composer — dark rounded card. The dedicated address selector +
            Fetch button that used to live above the textarea is gone:
            the backend now auto-resolves the user's saved Zomato address
            on the first food turn and the chat surfaces a click-to-switch
            picker under the assistant bubble whenever a new address pick
            is needed. */}
        <div className="relative bg-surface border border-border rounded-3xl shadow-lg shadow-black/40 focus-within:border-amber-500/40 focus-within:ring-4 focus-within:ring-amber-500/10 transition-all">
          <div className="flex items-end gap-2 p-2">
            <textarea
              ref={textareaRef}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask, write or search for anything..."
              rows={1}
              className="flex-1 bg-transparent text-gray-100 placeholder:text-gray-500 px-3 py-2 focus:outline-none resize-none min-h-[44px] text-[15px] leading-relaxed"
            />
            <button
              onClick={handleSubmit}
              disabled={!message.trim() || disabled}
              aria-label="Send message"
              className="w-10 h-10 rounded-full bg-accent text-gray-900 flex items-center justify-center disabled:opacity-30 disabled:grayscale transition-all hover:bg-accent-hover shadow-md shadow-amber-500/30 flex-shrink-0"
            >
              <ArrowUp size={18} strokeWidth={2.5} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
