import React, { useState, useRef, useEffect } from 'react';
import { Send, MapPin, ChevronDown } from 'lucide-react';
import type { Address } from './types';

interface MessageInputProps {
  onSendMessage: (message: string) => void;
  addresses: Address[];
  selectedAddressId: string;
  onAddressChange: (id: string) => void;
  onFetchAddresses: () => void;
  isLoadingAddresses: boolean;
  disabled: boolean;
}

export const MessageInput: React.FC<MessageInputProps> = ({
  onSendMessage,
  addresses,
  selectedAddressId,
  onAddressChange,
  onFetchAddresses,
  isLoadingAddresses,
  disabled
}) => {
  const [message, setMessage] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (message.trim() && !disabled && selectedAddressId) {
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
      <div className="max-w-4xl mx-auto space-y-4">
        {/* Quick Actions */}
        <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-none">
          {quickActions.map((action) => (
            <button
              key={action.label}
              onClick={() => setMessage(action.query)}
              className="px-4 py-1.5 rounded-full bg-surface border border-border text-xs font-semibold text-gray-400 hover:text-white hover:border-accent transition-all whitespace-nowrap"
            >
              {action.label}
            </button>
          ))}
        </div>

        {/* Address Selector & Input */}
        <div className="relative bg-surface border border-border rounded-2xl shadow-xl focus-within:border-accent/50 focus-within:ring-1 focus-within:ring-accent/50 transition-all">
          <div className="flex flex-col">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-border/50">
              <MapPin size={16} className="text-accent" />
              <select
                value={selectedAddressId}
                onChange={(e) => onAddressChange(e.target.value)}
                className="bg-transparent text-xs font-bold text-gray-300 focus:outline-none appearance-none cursor-pointer flex-1"
              >
                <option value="">Select Delivery Address</option>
                {addresses.map((addr) => (
                  <option key={addr.address_id} value={addr.address_id}>
                    {addr.location_name}
                  </option>
                ))}
              </select>
              <button 
                onClick={onFetchAddresses}
                disabled={isLoadingAddresses}
                className="text-[10px] font-black uppercase tracking-tighter text-accent hover:text-accent-hover disabled:opacity-50"
              >
                {isLoadingAddresses ? 'Loading...' : 'Fetch'}
              </button>
              <ChevronDown size={14} className="text-gray-500" />
            </div>

            <div className="flex items-end gap-2 p-2">
              <textarea
                ref={textareaRef}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask for food or groceries..."
                rows={1}
                className="flex-1 bg-transparent text-gray-200 px-3 py-2 focus:outline-none resize-none min-h-[44px] text-[15px] leading-relaxed"
              />
              <button
                onClick={handleSubmit}
                disabled={!message.trim() || !selectedAddressId || disabled}
                className="w-10 h-10 rounded-xl bg-accent text-white flex items-center justify-center disabled:opacity-30 disabled:grayscale transition-all shadow-lg shadow-accent/20 flex-shrink-0"
              >
                <Send size={18} />
              </button>
            </div>
          </div>
        </div>
        
        {!selectedAddressId && (
          <p className="text-[11px] text-center text-red-400/80 font-medium">
            Please select an address to start chatting
          </p>
        )}
      </div>
    </div>
  );
};
