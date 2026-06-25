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
import { Menu, Send, Sparkles, Car, Utensils, Calendar, Ticket, Search, X } from 'lucide-react';
import { motion } from 'motion/react';
import { ThinkingIndicator } from './ThinkingIndicator';
import { ChatMessageItem } from './ChatMessageItem';
import { AddressChip } from './AddressChip';
import { VoiceInput } from '../VoiceInput';
import { prefetchHeroGreeting, readCachedGreeting } from '../heroGreeting';
import type { GeoCoordinate, Message, SessionAddress } from '../types';

interface ChatAreaProps {
  messages: Message[];
  onSendMessage: (text: string) => void;
  isThinking: boolean;
  onSuggestionClick: (text: string) => void;
  /** Drops the in-chat grocery category picker card into the conversation
   *  (issue #59) — fired by the Groceries chip + the grocery hero nudge. */
  onOpenGroceries?: () => void;
  onOpenFood?: () => void;
  /** Drops the in-chat District discovery picker (movies / events / dining /
   *  theatres) into the conversation — fired by the Movies & Events chip. */
  onOpenDistrict?: () => void;
  onToggleLeft?: () => void;
  connectedCount?: number;
  isSidebarCollapsed?: boolean;
  username?: string;
  sessionAddresses?: SessionAddress[];
  onSelectAddress?: (addr: SessionAddress) => void;
  onQuickPrompt?: (prompt: string) => void;
  quickPromptDisabled?: boolean;
  onVoiceTurn?: (userText: string, assistantText: string) => void;
  onVoiceTurnComplete?: (userText: string, assistantText: string, hadToolCall: boolean) => void;
  onVoiceToolCall?: (intent: string) => Promise<string>;
  onVoiceStateChange?: (state: import('../types').VoiceSessionState) => void;
  onVoiceError?: (message: string) => void;
  onVoicePhase?: (phase: 'listening' | 'thinking' | 'speaking') => void;
  onVoiceCaption?: (role: 'user' | 'assistant', fullText: string) => void;
  // Live reader for the ongoing chat — voice (re)connect resumes it (#20).
  getVoiceContext?: () => { chatId: string | null; history: { role: string; content: string }[] };
  // Delivery address chip above the composer (food/grocery context).
  selectedAddressId?: string | null;
  onPickAddress?: (addr: SessionAddress) => void;
  /** Hero image shown on the empty state. Overridable so we don't
   *  hard-fail offline / blocked-CDN setups. */
  heroImageUrl?: string;
  onGrantLocation?: (geo: GeoCoordinate) => void;
}

/**
 * Wraps the user's name inside the AI greeting in the brand gradient
 * (saffron→rose). The greeting is a free-form server string, so the name is
 * located by matching name-ish tokens of the account handle ("admin_bhavesh"
 * → "Bhavesh"); when nothing matches, the greeting renders unstyled.
 */
const GENERIC_HANDLE_TOKENS = new Set(['admin', 'user', 'test', 'demo']);
function greetingWithAccent(text: string, username?: string): React.ReactNode {
  const tokens = (username || '')
    .split(/[^a-zA-Z]+/)
    .filter((t) => t.length >= 3 && !GENERIC_HANDLE_TOKENS.has(t.toLowerCase()));
  for (const token of tokens) {
    const match = text.match(new RegExp(`\\b${token}\\b`, 'i'));
    if (!match || match.index === undefined) continue;
    return (
      <>
        {text.slice(0, match.index)}
        <span className="bg-gradient-to-r from-[#ff8a1e] to-[#e85d7a] bg-clip-text text-transparent">
          {match[0]}
        </span>
        {text.slice(match.index + match[0].length)}
      </>
    );
  }
  return text;
}

// Prompt ribbon — same wording as the new UI; clicking a chip fires a
// real `/api/agent/query/` turn through the parent's onSuggestionClick.
const PROMPT_OPTIONS = [
  {
    id: 'groceries',
    label: 'Groceries',
    prompt: 'Search milk and brown bread on Swiggy Instamart',
    icon: Calendar,
    color:
      'bg-[#1a9d6b]/8 border-[#1a9d6b]/16 hover:border-[#1a9d6b]/45 text-[#147a53] hover:bg-[#1a9d6b]/12',
  },
  {
    id: 'cab',
    label: 'Book a Cab',
    prompt: 'Book a cab from my location to the office',
    icon: Car,
    color:
      'bg-[#2b2f72]/8 border-[#2b2f72]/15 hover:border-[#2b2f72]/45 text-[#2b2f72] hover:bg-[#2b2f72]/12',
  },
  {
    id: 'food',
    label: 'Order Food',
    prompt: 'Find good pizza near my saved address on Zomato',
    icon: Utensils,
    color:
      'bg-[#ff8a1e]/10 border-[#ff8a1e]/18 hover:border-[#ff8a1e]/50 text-[#c2630f] hover:bg-[#ff8a1e]/16',
  },
  {
    id: 'movie',
    label: 'Movies & Events',
    prompt: 'Find live music events near me on District',
    icon: Ticket,
    color:
      'bg-[#e85d7a]/8 border-[#e85d7a]/16 hover:border-[#e85d7a]/45 text-[#c23e5b] hover:bg-[#e85d7a]/12',
  },
];

export function ChatArea({
  messages,
  onSendMessage,
  isThinking,
  onSuggestionClick,
  onOpenGroceries,
  onOpenFood,
  onOpenDistrict,
  onToggleLeft,
  connectedCount: _connectedCount = 0,
  isSidebarCollapsed = false,
  username,
  sessionAddresses = [],
  onSelectAddress,
  onQuickPrompt,
  quickPromptDisabled,
  onVoiceTurn,
  onVoiceTurnComplete,
  onVoiceToolCall,
  onVoiceStateChange,
  onVoiceError,
  onVoicePhase,
  onVoiceCaption,
  getVoiceContext,
  selectedAddressId,
  onPickAddress,
  onGrantLocation,
}: ChatAreaProps) {
  const [inputValue, setInputValue] = useState('');
  // In-chat message search — preserved from the prior skin so users can
  // still scroll back through a long conversation by keyword. Toggled
  // from the header search icon; filters `messages` to those whose
  // markdown body contains the query (case-insensitive). Empty query =
  // show everything.
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  // Ribbon / nudge dispatcher: the Groceries chip (issue #59) drops an
  // in-chat category picker card into the conversation instead of firing a
  // fixed prompt; every other chip fires its prompt immediately, as before.
  const handleQuickAction = (option: { id: string; prompt: string }) => {
    if (isThinking) return;
    if (option.id === 'groceries' && onOpenGroceries) {
      onOpenGroceries();
      return;
    }
    if (option.id === 'food' && onOpenFood) {
      onOpenFood();
      return;
    }
    if (option.id === 'movie' && onOpenDistrict) {
      onOpenDistrict();
      return;
    }
    onSuggestionClick(option.prompt);
  };
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const lastMessageRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    requestAnimationFrame(() => {
      const container = scrollRef.current;
      if (!container) return;
      if (isThinking) {
        // Show the thinking indicator at the bottom
        container.scrollTop = container.scrollHeight;
        return;
      }
      // Scroll so the top of the latest message is visible near the top
      const lastMsg = lastMessageRef.current;
      if (lastMsg) {
        const containerTop = container.getBoundingClientRect().top;
        const msgTop = lastMsg.getBoundingClientRect().top;
        container.scrollTop += msgTop - containerTop - 16;
      } else {
        container.scrollTop = container.scrollHeight;
      }
    });
  }, [messages, isThinking]);

  // Auto-grow the textarea up to ~6 lines so multi-line input feels as
  // natural as the previous MessageInput's textarea. Cap matches the
  // old skin so a runaway paste doesn't eat the whole chat area.
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    if (!inputValue.trim()) {
      ta.style.height = '24px';
      return;
    }
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

  // Account chip in the header — the raw handle is fine THERE, never in the hero.

  // Personalized AI hero greeting — generated server-side each load from the
  // user's profile, live weather and time of day. The fetch is started early
  // (login / app boot) via prefetchHeroGreeting; this consumes the shared
  // promise. First paint uses the last generated line cached per user (else a
  // neutral "Namaste."), then crossfades to the fresh line when it arrives.
  const [greeting, setGreeting] = useState<string>(
    () => readCachedGreeting(username) || 'Namaste.',
  );
  const [greetingReady, setGreetingReady] = useState(false);
  const [greetingSwapping, setGreetingSwapping] = useState(false);
  const greetingRef = useRef(greeting);
  greetingRef.current = greeting;
  useEffect(() => {
    let alive = true;
    const cached = readCachedGreeting(username);
    if (cached) setGreeting(cached);
    prefetchHeroGreeting(username).then((fresh) => {
      if (!alive || !fresh) return;
      if (fresh === greetingRef.current) {
        setGreetingReady(true);
        return;
      }
      setGreetingSwapping(true);
      window.setTimeout(() => {
        if (!alive) return;
        setGreeting(fresh);
        setGreetingSwapping(false);
        setGreetingReady(true);
      }, 320);
    });
    return () => {
      alive = false;
    };
  }, [username]);



  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden relative font-sans select-none">
      {/* Header */}
      <header className="shrink-0 bg-white/46 border-b border-[#ece3d6]/60 px-3 sm:px-5 py-2.5 sm:py-3.5 flex items-center justify-between backdrop-blur-md sticky top-0 z-30 select-none">
        <div className="flex items-center gap-3">
          {isSidebarCollapsed && (
            <button
              type="button"
              onClick={onToggleLeft}
              className="p-2 rounded-xl text-[#2b2f72] bg-[#2b2f72]/8 border border-[#2b2f72]/20 hover:bg-[#2b2f72]/12 hover:border-[#2b2f72]/35 shadow-sm transition-all duration-300 active:scale-95 cursor-pointer"
              title="Expand sidebar"
            >
              <Menu className="w-5 h-5 stroke-[2.2]" />
            </button>
          )}

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
            className={`w-9 h-9 rounded-[10px] border border-[#ece3d6]/80 text-[#7a7388] bg-white/60 hover:text-[#221b2e] hover:bg-white flex items-center justify-center transition-all cursor-pointer ${
              searchOpen ? 'bg-white text-[#221b2e]' : ''
            }`}
            title={searchOpen ? 'Close search' : 'Search this conversation'}
            aria-label="Search conversation"
          >
            <Search className="w-4 h-4 stroke-[2.2]" />
          </button>

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
              <div className="h-full min-h-0 flex flex-col justify-start md:justify-center items-center text-center px-5 py-1 md:py-6 select-none w-full max-w-4xl mx-auto z-10 relative overflow-y-auto overflow-x-hidden md:overflow-hidden no-scrollbar">
                <motion.div
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.05, duration: 0.6, ease: 'easeOut' }}
                  className="relative z-10 my-auto flex flex-col items-center"
                >
                  {/* Brand logo mark */}
                  <div
                    className="w-14 h-14 rounded-2xl mb-5 flex items-center justify-center shadow-md"
                    style={{ background: 'linear-gradient(135deg, #2b2f72 0%, #6c4fbd 100%)' }}
                  >
                    <span className="text-white font-bold text-2xl leading-none select-none">C</span>
                  </div>

                  {/* Headline — AI-generated personalized greeting (name, time of
                      day, local weather, interests); the user's name renders in
                      the brand gradient. Height is reserved for two lines so the
                      card below doesn't jump when the fresh line crossfades in. */}
                  <h1
                    className={`font-display font-semibold text-[clamp(28px,5vw,46px)] leading-[1.08] tracking-tight mb-6 text-[#221b2e] max-w-[680px] min-h-[3.3em] md:min-h-[2.2em] flex items-center justify-center transition-all duration-500 ease-out ${
                      greetingSwapping
                        ? 'opacity-0 blur-[7px] -translate-y-1'
                        : greetingReady
                          ? 'opacity-100 blur-0 translate-y-0'
                          : 'opacity-85'
                    }`}
                  >
                    <span>{greetingWithAccent(greeting, username)}</span>
                  </h1>

                </motion.div>
              </div>
            ) : (
              <div className="w-full space-y-2 pb-6">
                {searchQuery.trim() && visibleMessages.length === 0 && (
                  <div className="text-center text-xs text-slate-500 italic">
                    No messages match “{searchQuery}”.
                  </div>
                )}
                {(() => {
                  // Always scroll to the last user message so the question sits at
                  // the top and the answer scrolls into view naturally below it.
                  const lastUserIdx = visibleMessages.reduce(
                    (found, m, i) => (m.role === 'user' ? i : found),
                    visibleMessages.length - 1,
                  );
                  return visibleMessages.map((msg, i) => {
                  const index = messages.indexOf(msg);
                  return (
                    <div key={msg.message_id || `${msg.role}-${index}-${msg.content.slice(0, 20)}`} ref={i === lastUserIdx ? lastMessageRef : undefined}>
                      <ChatMessageItem
                        msg={msg}
                        isLatestAssistant={index === lastAssistantIdx}
                        isThinking={isThinking}
                        sessionAddresses={sessionAddresses}
                        onSelectAddress={onSelectAddress}
                        onQuickPrompt={onQuickPrompt}
                        quickPromptDisabled={quickPromptDisabled}
                        onGrantLocation={onGrantLocation}
                      />
                    </div>
                  );
                  });
                })()}

                {isThinking && <ThinkingIndicator />}
              </div>
            )}
          </div>

          {/* Bottom composer */}
          <div className="px-3 sm:px-4 pt-2.5 sm:pt-4 pb-[calc(0.75rem+var(--safe-bottom))] sm:pb-[calc(1rem+var(--safe-bottom))] md:pb-4 bg-gradient-to-t from-[#F3F7FD]/98 via-[#F3F7FD]/50 to-transparent sticky bottom-0 left-0 right-0 z-20">
            {/* Delivery address chip — visible + switchable BEFORE any
                food/grocery search runs (no more wrong-house surprises). */}
            {sessionAddresses.length > 0 && onPickAddress && (
              <div className="mb-2">
                <AddressChip
                  addresses={sessionAddresses}
                  selectedId={selectedAddressId}
                  onSelect={onPickAddress}
                />
              </div>
            )}
            {/* Prompt ribbon — switches to grocery quick items when grocery flow is active */}
            {(() => {
              const isGroceryMode = messages.some(
                (m) => m.attachments?.some((a) => a.kind === 'grocery_categories'),
              );
              // No quick-item chips while shopping (removed per request) — keep the composer clean.
              if (isGroceryMode) return null;
              return (
                <div className="w-full flex items-center gap-1.5 sm:gap-2 overflow-x-auto pb-1 mb-1 no-scrollbar scroll-smooth">
                  {PROMPT_OPTIONS.map((option) => {
                    const IconComponent = option.icon;
                    return (
                      <button
                        key={option.id}
                        type="button"
                        onClick={() => handleQuickAction(option)}
                        disabled={isThinking}
                        className={`shrink-0 flex items-center gap-1 sm:gap-1.5 border px-3 py-1.5 rounded-[11px] sm:rounded-[12px] text-[11px] sm:text-[12px] font-semibold transition-all duration-200 active:scale-95 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 shadow-[0_6px_18px_-14px_rgba(60,40,30,0.4)] hover:shadow-sm ${option.color}`}
                      >
                        <div className="p-0.5 rounded-md shrink-0">
                          <IconComponent className="w-3 h-3 sm:w-3.5 sm:h-3.5" />
                        </div>
                        <span>{option.label}</span>
                      </button>
                    );
                  })}
                </div>
              );
            })()}

            <form
              onSubmit={handleSubmit}
              className="w-full bg-white shadow-[0_2px_16px_rgba(0,0,0,0.08)] rounded-2xl flex items-center pl-4 pr-2 py-3 gap-2 transition-all duration-200"
            >
              {/* Sparkle icon */}
              <span className="shrink-0 w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center text-slate-400">
                <Sparkles className="w-4 h-4" />
              </span>

              <textarea
                ref={textareaRef}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask omi anything…"
                disabled={isThinking}
                rows={1}
                className="flex-1 bg-transparent border-none outline-none text-[14px] text-[#1a1530] placeholder-slate-400 focus:ring-0 min-w-0 resize-none overflow-hidden self-center"
                style={{ height: '24px', lineHeight: '24px', padding: 0 }}
              />

              <div className="shrink-0 [&_button]:!w-8 [&_button]:!h-8 [&_button]:!rounded-full [&_button]:!bg-slate-100 [&_button]:!text-slate-500">
                <VoiceInput
                  onVoiceTurn={onVoiceTurn}
                  onVoiceTurnComplete={onVoiceTurnComplete}
                  onVoiceStateChange={onVoiceStateChange}
                  onVoiceToolCall={onVoiceToolCall}
                  onVoiceError={onVoiceError}
                  onVoicePhase={onVoicePhase}
                  onVoiceCaption={onVoiceCaption}
                  getVoiceContext={getVoiceContext}
                  disabled={isThinking}
                />
              </div>

              <button
                type="submit"
                disabled={!inputValue.trim() || isThinking}
                aria-label="Send message"
                className={`w-8 h-8 rounded-full flex items-center justify-center transition-all shrink-0 cursor-pointer ${
                  inputValue.trim() && !isThinking
                    ? 'bg-[#7B6FBE] text-white hover:bg-[#6a5fad] active:scale-95'
                    : 'bg-[#EAE8F5] text-[#B8B0D8] cursor-not-allowed'
                }`}
              >
                <Send className="w-3.5 h-3.5 stroke-[2]" />
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
