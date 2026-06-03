/**
 * Center canvas — the redesigned shell (header + hero empty-state +
 * scrollable message list + bottom composer) wrapped around the
 * existing chat plumbing (real Message + Attachment shape, real
 * /api/agent/query/ flow, real VoiceInput recorder + transcribe).
 *
 * Drops the new UI's simulated mic in favour of `<VoiceInput />`
 * (audio recording → Sarvam transcribe → onTranscript) so dictation
 * works end-to-end, and drops the cosmetic in-line "MCP call" panel
 * (the chat already surfaces tool activity via meta chips + cards).
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Menu, Send, Paperclip, Car, Utensils, Calendar, Ticket, Bell, Search, X } from 'lucide-react';
import { motion } from 'motion/react';
import { ChatMessageItem } from './ChatMessageItem';
import { VoiceInput } from '../VoiceInput';
import type { Message, SessionAddress } from '../types';

interface ChatAreaProps {
  messages: Message[];
  onSendMessage: (text: string) => void;
  isThinking: boolean;
  onSuggestionClick: (text: string) => void;
  onToggleLeft?: () => void;
  connectedCount?: number;
  isSidebarCollapsed?: boolean;
  username?: string;
  sessionAddresses?: SessionAddress[];
  onSelectAddress?: (addr: SessionAddress) => void;
  onQuickPrompt?: (prompt: string) => void;
  quickPromptDisabled?: boolean;
  /** Hero image shown on the empty state. Overridable so we don't
   *  hard-fail offline / blocked-CDN setups. */
  heroImageUrl?: string;
}

// Prompt ribbon — same wording as the new UI; clicking a chip fires a
// real `/api/agent/query/` turn through the parent's onSuggestionClick.
const PROMPT_OPTIONS = [
  {
    id: 'cab',
    label: 'Book a Cab',
    prompt: 'Book a cab from my location to the office',
    icon: Car,
    color: 'bg-blue-50/70 border-blue-100 hover:border-blue-400 text-blue-600 hover:bg-blue-50/95',
  },
  {
    id: 'food',
    label: 'Order Food',
    prompt: 'Find good pizza near my saved address on Zomato',
    icon: Utensils,
    color:
      'bg-orange-50/70 border-orange-100 hover:border-orange-400 text-orange-600 hover:bg-orange-50/95',
  },
  {
    id: 'groceries',
    label: 'Groceries',
    prompt: 'Search milk and brown bread on Swiggy Instamart',
    icon: Calendar,
    color:
      'bg-emerald-50/70 border-emerald-100 hover:border-emerald-400 text-emerald-600 hover:bg-emerald-50/95',
  },
  {
    id: 'movie',
    label: 'Find Events',
    prompt: 'Find live music events near me on District',
    icon: Ticket,
    color:
      'bg-indigo-50/70 border-indigo-100 hover:border-indigo-400 text-indigo-600 hover:bg-indigo-50/95',
  },
  {
    id: 'reminder',
    label: 'My Cart',
    prompt: "What's currently in my cart?",
    icon: Bell,
    color:
      'bg-purple-50/70 border-purple-100 hover:border-purple-400 text-purple-600 hover:bg-purple-50/95',
  },
];

const DEFAULT_HERO = 'https://i.postimg.cc/pT4dvmdP/pakkaaaaa.png';

export function ChatArea({
  messages,
  onSendMessage,
  isThinking,
  onSuggestionClick,
  onToggleLeft,
  connectedCount = 0,
  isSidebarCollapsed = false,
  username,
  sessionAddresses = [],
  onSelectAddress,
  onQuickPrompt,
  quickPromptDisabled,
  heroImageUrl = DEFAULT_HERO,
}: ChatAreaProps) {
  const [inputValue, setInputValue] = useState('');
  // In-chat message search — preserved from the prior skin so users can
  // still scroll back through a long conversation by keyword. Toggled
  // from the header search icon; filters `messages` to those whose
  // markdown body contains the query (case-insensitive). Empty query =
  // show everything.
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isThinking]);

  // Auto-grow the textarea up to ~6 lines so multi-line input feels as
  // natural as the previous MessageInput's textarea. Cap matches the
  // old skin so a runaway paste doesn't eat the whole chat area.
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }, [inputValue]);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    const text = inputValue.trim();
    if (!text || isThinking) return;
    onSendMessage(text);
    setInputValue('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends; Shift+Enter (or any modifier) inserts a newline so
    // multi-line prompts and Hinglish dictation with line breaks work.
    if (e.key === 'Enter' && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const visibleMessages = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return messages;
    return messages.filter((m) => (m.content || '').toLowerCase().includes(q));
  }, [messages, searchQuery]);

  // Latest-assistant index — the only message eligible to surface the
  // address-choice chips. Computed against the FULL message list, not
  // the filtered view, so search doesn't accidentally promote an older
  // assistant turn that happens to match the query.
  let lastAssistantIdx = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'assistant') {
      lastAssistantIdx = i;
      break;
    }
  }

  const welcomeName = (username && username.trim()) || 'there';
  const avatarLetter = welcomeName.charAt(0).toUpperCase() || 'C';

  return (
    <div className="flex-1 flex flex-col h-full bg-gradient-to-tr from-[#fafbf7] via-[#f1f7fe] to-[#edf5ff] overflow-hidden relative font-sans select-none">
      {/* Background mesh — sunlit amber + sky-blue + white */}
      <div className="absolute inset-0 pointer-events-none z-0 overflow-hidden">
        <div className="absolute top-[15%] -left-[10%] w-[55%] h-[50%] rounded-full bg-[#fdf8d7]/70 filter blur-[110px] opacity-75" />
        <div className="absolute -bottom-[12%] right-[5%] w-[60%] h-[55%] rounded-full bg-[#dbeeff]/75 filter blur-[125px] opacity-80" />
        <div className="absolute top-[-5%] right-[10%] w-[50%] h-[45%] rounded-full bg-white/95 filter blur-[100px] opacity-95" />
        <div className="absolute top-[35%] left-[20%] w-[45%] h-[45%] rounded-full bg-[#e3f4f1]/55 filter blur-[110px] opacity-70" />
      </div>

      {/* Header */}
      <header className="shrink-0 bg-white/50 border-b border-slate-200/40 px-5 py-3.5 flex items-center justify-between backdrop-blur-md sticky top-0 z-30 select-none">
        <div className="flex items-center gap-3">
          {isSidebarCollapsed && (
            <button
              type="button"
              onClick={onToggleLeft}
              className="p-2 rounded-xl text-indigo-600 bg-indigo-50 border border-indigo-200 hover:bg-indigo-100/80 hover:border-indigo-300 shadow-sm transition-all duration-300 active:scale-95 cursor-pointer"
              title="Expand sidebar"
            >
              <Menu className="w-5 h-5 stroke-[2.2]" />
            </button>
          )}

          <div className="flex items-center gap-2">
            <span className="font-bold text-slate-800 text-[15px] tracking-tight font-display">
              Companion
            </span>
            <span className="hidden sm:inline-block bg-emerald-500/10 text-emerald-700 border border-emerald-500/20 px-2 py-0.5 rounded-full text-[10px] font-semibold">
              Ready
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Search button — opens an inline search pill that filters
              the visible messages by text (preserved from the prior
              skin so users can still grep a long thread). */}
          <button
            type="button"
            onClick={() => {
              setSearchOpen((v) => {
                if (v) setSearchQuery('');
                return !v;
              });
            }}
            className={`p-2 rounded-full text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition-all cursor-pointer ${
              searchOpen ? 'bg-slate-100 text-slate-900' : ''
            }`}
            title={searchOpen ? 'Close search' : 'Search this conversation'}
            aria-label="Search conversation"
          >
            <Search className="w-4 h-4 stroke-[2.2]" />
          </button>

          {/* User pill */}
          <div className="flex items-center gap-2 bg-white/90 text-slate-800 border border-slate-200/70 pl-2 pr-3.5 py-1.5 rounded-full text-xs font-bold shadow-3xs max-h-9 select-none">
            <div className="w-5.5 h-5.5 rounded-full bg-gradient-to-tr from-indigo-600 to-violet-600 text-white flex items-center justify-center font-bold text-[10.5px] shadow-sm select-none">
              {avatarLetter}
            </div>
            <span className="hidden sm:inline text-slate-700 font-display capitalize">
              {welcomeName}
            </span>
            {connectedCount > 0 && (
              <span className="bg-emerald-500 text-white text-[10px] w-5 h-5 rounded-full flex items-center justify-center font-mono font-bold">
                {connectedCount}
              </span>
            )}
          </div>
        </div>
      </header>

      {searchOpen && (
        <div className="absolute top-16 left-1/2 -translate-x-1/2 z-30 flex items-center gap-2 bg-white border border-slate-200 rounded-full shadow-lg shadow-slate-900/10 pl-3 pr-1 py-1 max-w-[92vw]">
          <Search className="w-3.5 h-3.5 text-slate-500 shrink-0" />
          <input
            autoFocus
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search this conversation…"
            className="bg-transparent text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none w-48 md:w-64"
          />
          <button
            onClick={() => {
              setSearchOpen(false);
              setSearchQuery('');
            }}
            className="p-1.5 text-slate-500 hover:text-slate-900 hover:bg-slate-100 rounded-full transition-colors shrink-0"
            title="Close search"
            aria-label="Close search"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Body */}
      <div className="flex-1 overflow-hidden relative z-10 flex flex-col">
        <div className="flex-1 flex flex-col h-full overflow-hidden">
          <div
            ref={scrollRef}
            className="flex-1 overflow-y-auto px-4 md:px-8 py-6 space-y-6 no-scrollbar"
          >
            {messages.length === 0 ? (
              <div className="h-full min-h-0 flex flex-col justify-center items-center px-4 py-1.5 md:py-2 space-y-4 md:space-y-6 select-none w-full max-w-4xl mx-auto z-10 relative">
                <motion.div
                  initial={{ opacity: 0, y: -15 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.1, duration: 0.6, ease: 'easeOut' }}
                  className="w-full flex flex-col md:flex-row items-center justify-between gap-3 md:gap-6 relative shrink-0"
                >
                  <div className="space-y-1 flex-1 text-center md:text-left">
                    <h1 className="font-display font-black text-slate-900 text-2xl md:text-[36px] tracking-tight leading-tight capitalize">
                      Hi {welcomeName}, how can I help you today?
                    </h1>
                  </div>

                  <div className="flex items-center gap-2.5 shrink-0 relative mt-1 md:mt-0">
                    <div className="mb-2 relative bg-white border border-slate-150/85 px-3 py-1.5 rounded-2xl shadow-sm text-center">
                      <span className="text-[10px] font-bold text-slate-900 block leading-tight font-sans">
                        Hey there! 👋
                      </span>
                      <span className="text-[10px] font-bold text-slate-800 block leading-tight font-sans mt-0.5">
                        Need a boost?
                      </span>
                      <div className="absolute right-[-5px] top-5 w-2.5 h-2.5 bg-white border-r border-b border-slate-100 rotate-[-45deg] z-10" />
                    </div>

                    <div
                      className="w-14 h-14 bg-gradient-to-b from-white to-slate-100 rounded-full border border-slate-200 shadow-md flex items-center justify-center relative animate-bounce shrink-0"
                      style={{ animationDuration: '4s' }}
                    >
                      <div className="w-11 h-8 bg-[#121420] rounded-[12px] border border-slate-700 flex items-center justify-center relative shadow-inner overflow-hidden">
                        <div className="flex items-center gap-1">
                          <span className="text-cyan-400 text-xs font-black leading-none animate-pulse">
                            ^
                          </span>
                          <span className="text-cyan-400 text-xs font-black leading-none animate-pulse">
                            ^
                          </span>
                        </div>
                        <div className="w-full h-[1px] bg-cyan-400/20 absolute top-2.5 left-0 animate-pulse" />
                      </div>
                      <div className="absolute -left-1 top-5 w-2 h-4.5 bg-slate-100 border border-slate-200/80 rounded-full transform -rotate-12 shadow-3xs" />
                      <div className="absolute -right-1 top-5 w-2 h-4.5 bg-slate-100 border border-slate-200/80 rounded-full transform rotate-12 shadow-3xs" />
                      <div className="absolute top-0.5 left-1.5 w-3 h-1 bg-white/40 rounded-full filter blur-[0.5px]" />
                    </div>
                  </div>
                </motion.div>

                {/* Hero image */}
                <div className="w-full relative z-10 flex flex-col items-center justify-center min-h-0 flex-1 max-h-[48vh] md:max-h-[58vh] -mt-1 md:-mt-3">
                  <div className="relative w-full max-w-lg md:max-w-2xl lg:max-w-3xl mx-auto h-full flex items-center justify-center transition-transform duration-500 hover:scale-[1.015]">
                    <div className="absolute inset-x-12 top-4 bottom-4 bg-gradient-to-tr from-sky-300/20 via-pink-350/15 to-indigo-400/20 rounded-full filter blur-[45px] opacity-75 pointer-events-none" />
                    <motion.img
                      initial={{ opacity: 0, scale: 0.95 }}
                      animate={{ opacity: 1, scale: 1 }}
                      transition={{ type: 'spring', stiffness: 70, damping: 15, duration: 1.0 }}
                      src={heroImageUrl}
                      alt="App Companions Collage"
                      className="max-w-full max-h-full object-contain relative z-10 drop-shadow-[0_12px_28px_rgba(30,41,59,0.12)]"
                      referrerPolicy="no-referrer"
                    />
                  </div>
                </div>
              </div>
            ) : (
              <div className="w-full space-y-6 pb-6">
                {searchQuery.trim() && visibleMessages.length === 0 && (
                  <div className="text-center text-xs text-slate-500 italic">
                    No messages match “{searchQuery}”.
                  </div>
                )}
                {visibleMessages.map((msg) => {
                  const index = messages.indexOf(msg);
                  return (
                    <ChatMessageItem
                      key={msg.message_id || `${msg.role}-${index}-${msg.content.slice(0, 20)}`}
                      msg={msg}
                      isLatestAssistant={index === lastAssistantIdx}
                      isThinking={isThinking}
                      sessionAddresses={sessionAddresses}
                      onSelectAddress={onSelectAddress}
                      onQuickPrompt={onQuickPrompt}
                      quickPromptDisabled={quickPromptDisabled}
                    />
                  );
                })}

                {isThinking && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className="flex items-center gap-3 pl-3"
                  >
                    <div className="w-8 h-8 rounded-full bg-slate-900 text-white flex items-center justify-center font-bold text-[12px] font-display">
                      C
                    </div>
                    <div className="bg-white/80 border border-slate-200/60 py-2.5 px-4 rounded-full flex items-center gap-2 shadow-xs backdrop-blur-xs">
                      <span className="w-1.5 h-1.5 bg-indigo-600 rounded-full animate-ping" />
                      <span className="text-slate-500 font-medium text-xs italic">
                        Thinking…
                      </span>
                    </div>
                  </motion.div>
                )}
              </div>
            )}
          </div>

          {/* Bottom composer */}
          <div className="p-4 bg-gradient-to-t from-[#edf5ff]/95 via-[#edf5ff]/20 to-transparent sticky bottom-0 left-0 right-0 z-20">
            {/* Prompt ribbon */}
            <div className="w-full flex items-center gap-2 overflow-x-auto pb-3 mb-1.5 no-scrollbar scroll-smooth">
              {PROMPT_OPTIONS.map((option) => {
                const IconComponent = option.icon;
                return (
                  <button
                    key={option.id}
                    type="button"
                    onClick={() => onSuggestionClick(option.prompt)}
                    disabled={isThinking}
                    className={`shrink-0 flex items-center gap-2 border px-3 py-1.5 rounded-full text-xs font-bold transition-all duration-200 active:scale-95 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 shadow-[0_1px_2px_rgba(0,0,0,0.03)] hover:shadow-sm ${option.color}`}
                  >
                    <div className="p-1 rounded-md shrink-0">
                      <IconComponent className="w-3.5 h-3.5" />
                    </div>
                    <span>{option.label}</span>
                  </button>
                );
              })}
            </div>

            <form
              onSubmit={handleSubmit}
              className="w-full bg-white border border-slate-200/80 shadow-md shadow-slate-300/10 rounded-3xl flex items-end p-1.5 gap-2"
            >
              <button
                type="button"
                tabIndex={-1}
                className="w-9 h-9 text-slate-400 hover:text-slate-650 hover:bg-slate-50 rounded-full flex items-center justify-center transition-colors shrink-0 cursor-pointer self-end mb-0.5"
                title="Attachment menu (coming soon)"
              >
                <Paperclip className="w-4.5 h-4.5" />
              </button>

              {/* Multi-line textarea (Enter to send, Shift+Enter for newline)
                  preserves the prior MessageInput's behaviour and lets
                  users paste multi-line prompts cleanly. Auto-grows up
                  to ~6 lines, then scrolls. */}
              <textarea
                ref={textareaRef}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask Companion anything…"
                disabled={isThinking}
                rows={1}
                className="flex-1 bg-transparent border-none outline-none text-[13.5px] text-slate-800 placeholder-slate-400 py-2.5 px-1 focus:ring-0 min-w-0 resize-none max-h-40 leading-relaxed"
              />

              {/* Real voice input (audio capture → Sarvam transcribe). On
                  auto-submit it fires whatever was just typed/transcribed. */}
              <div className="self-end mb-0.5">
                <VoiceInput
                  onTranscript={(text) =>
                    setInputValue((prev) => (prev ? `${prev} ${text}` : text))
                  }
                  onAutoSubmit={() => handleSubmit()}
                  autoSubmit
                  disabled={isThinking}
                />
              </div>

              <button
                type="submit"
                disabled={!inputValue.trim() || isThinking}
                aria-label="Send message"
                className={`w-9.5 h-9.5 rounded-full flex items-center justify-center transition-all shrink-0 cursor-pointer self-end mb-0.5 ${
                  inputValue.trim() && !isThinking
                    ? 'bg-slate-900 opacity-100 text-white shadow-md hover:bg-slate-800'
                    : 'bg-slate-100 text-slate-300'
                }`}
              >
                <Send className="w-4 h-4 stroke-[2.2]" />
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
