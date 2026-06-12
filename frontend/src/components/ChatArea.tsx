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
import { AddressChip } from './AddressChip';
import { VoiceInput } from '../VoiceInput';
import { INDIA_FACTS, nextFactIndex } from '../data/indiaFacts';
import { Mandala } from './Mandala';
import { SmartCard, type SmartCardSlide } from './SmartCard';
import { prefetchHeroGreeting, readCachedGreeting } from '../heroGreeting';
import type { Message, SessionAddress } from '../types';

interface ChatAreaProps {
  messages: Message[];
  onSendMessage: (text: string) => void;
  isThinking: boolean;
  /** Rolling human status from the in-flight turn ("Searching Zomato…"). */
  thinkingStatus?: string | null;
  onSuggestionClick: (text: string) => void;
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
    id: 'groceries',
    label: 'Groceries',
    prompt: 'Search milk and brown bread on Swiggy Instamart',
    icon: Calendar,
    color:
      'bg-[#1a9d6b]/8 border-[#1a9d6b]/16 hover:border-[#1a9d6b]/45 text-[#147a53] hover:bg-[#1a9d6b]/12',
  },
  {
    id: 'movie',
    label: 'Find Events',
    prompt: 'Find live music events near me on District',
    icon: Ticket,
    color:
      'bg-[#e85d7a]/8 border-[#e85d7a]/16 hover:border-[#e85d7a]/45 text-[#c23e5b] hover:bg-[#e85d7a]/12',
  },
  {
    id: 'reminder',
    label: 'My Cart',
    prompt: "What's currently in my cart?",
    icon: Bell,
    color:
      'bg-[#ffb347]/12 border-[#ffb347]/20 hover:border-[#ffb347]/55 text-[#b07410] hover:bg-[#ffb347]/18',
  },
];

export function ChatArea({
  messages,
  onSendMessage,
  isThinking,
  thinkingStatus,
  onSuggestionClick,
  onToggleLeft,
  connectedCount = 0,
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
    if (!inputValue.trim()) {
      ta.style.height = '36px';
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
  const welcomeName = (username && username.trim()) || 'there';
  const avatarLetter = welcomeName.charAt(0).toUpperCase() || 'C';

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

  // Heritage fact shown in the smart card; advances when the card wraps.
  const [factIdx, setFactIdx] = useState(() => Math.floor(Math.random() * INDIA_FACTS.length));
  const heritageFact = INDIA_FACTS[factIdx];

  const heroSlides = useMemo<SmartCardSlide[]>(() => {
    const promptOf = (id: string) => PROMPT_OPTIONS.find((o) => o.id === id)?.prompt ?? '';
    const hour = new Date().getHours();
    const nudge =
      hour < 5
        ? { eyebrow: 'Up late?', icon: '🌙', body: 'Midnight craving? Quick commerce is still awake.', label: 'Order a snack', prompt: promptOf('groceries') }
        : hour < 11
          ? { eyebrow: 'This morning', icon: '🌅', body: 'Chai, milk, bread — morning essentials at your door in minutes.', label: 'Order groceries', prompt: promptOf('groceries') }
          : hour < 16
            ? { eyebrow: 'Lunchtime', icon: '🍱', body: 'Beat the 1 PM rush — order lunch before the queues form.', label: 'Order food', prompt: promptOf('food') }
            : hour < 20
              ? { eyebrow: 'This evening', icon: '🎶', body: "Plans tonight? See what's on around you.", label: 'Find events', prompt: promptOf('movie') }
              : { eyebrow: 'Tonight', icon: '🍽', body: 'Dinner sorted? Order now and skip the late rush.', label: 'Order dinner', prompt: promptOf('food') };
    return [
      { id: 'fact', eyebrow: 'Did you know', icon: '💡', tone: 'saffron', body: heritageFact },
      {
        id: 'nudge',
        eyebrow: nudge.eyebrow,
        icon: nudge.icon,
        tone: 'indigo',
        body: nudge.body,
        cta: { label: nudge.label, onClick: () => onSuggestionClick(nudge.prompt) },
      },
      {
        id: 'cart',
        eyebrow: 'Pick up where you left off',
        icon: '🛒',
        tone: 'green',
        body: 'Anything waiting in your cart? Give it a quick look before it goes stale.',
        cta: { label: 'Open my cart', onClick: () => onSuggestionClick(promptOf('reminder')) },
      },
    ];
  }, [heritageFact, onSuggestionClick]);

  // Big, continuously-morphing mandala behind the hero: two stacked layers
  // crossfade to a freshly-generated pattern every ~7s (CSS adds a slow spin),
  // so it's always gently changing.
  const [mandalaSeeds, setMandalaSeeds] = useState<[number, number]>(() => [
    Math.floor(Math.random() * 1e9),
    Math.floor(Math.random() * 1e9),
  ]);
  const [mandalaActive, setMandalaActive] = useState(0);
  useEffect(() => {
    const t = setInterval(() => {
      setMandalaActive((a) => {
        const next = a === 0 ? 1 : 0;
        setMandalaSeeds((s) =>
          next === 0
            ? [Math.floor(Math.random() * 1e9), s[1]]
            : [s[0], Math.floor(Math.random() * 1e9)],
        );
        return next;
      });
    }, 11000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden relative font-sans select-none">
      {/* Header */}
      <header className="shrink-0 bg-white/46 border-b border-[#ece3d6]/60 px-5 py-3.5 flex items-center justify-between backdrop-blur-md sticky top-0 z-30 select-none">
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

          <div className="flex items-center gap-1">
            <span className="text-[#221b2e] font-semibold text-[15px] tracking-tight font-display">
              Companion
            </span>
            {/* Subtle tricolor nod — green / white gap / saffron dots */}
            <span
              aria-hidden
              className="hidden sm:inline-block w-2 h-2 rounded-full bg-[#1a9d6b] ml-7"
              style={{ boxShadow: '-11px 0 0 #fff, -22px 0 0 #ff8a1e' }}
            />
            <span className="hidden sm:inline text-[9px] font-bold tracking-[0.18em] uppercase text-[#a89db4] ml-3 mt-px">
              From Bharat, For Bharat
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
            className={`w-9 h-9 rounded-[10px] border border-[#ece3d6]/80 text-[#7a7388] bg-white/60 hover:text-[#221b2e] hover:bg-white flex items-center justify-center transition-all cursor-pointer ${
              searchOpen ? 'bg-white text-[#221b2e]' : ''
            }`}
            title={searchOpen ? 'Close search' : 'Search this conversation'}
            aria-label="Search conversation"
          >
            <Search className="w-4 h-4 stroke-[2.2]" />
          </button>

          {/* User pill */}
          <div className="flex items-center gap-2 bg-white/60 text-[#221b2e] border border-[#ece3d6]/80 pl-1.5 pr-3.5 py-1 rounded-[13px] text-xs font-bold shadow-3xs max-h-9 select-none">
            <div className="w-6 h-6 rounded-[8px] bg-[#221b2e] text-white flex items-center justify-center font-bold text-[11px] shadow-sm select-none">
              {avatarLetter}
            </div>
            <span className="hidden sm:inline text-[#221b2e] font-display capitalize">
              {welcomeName}
            </span>
            {connectedCount > 0 && (
              <span className="bg-[#1a9d6b] text-white text-[10px] w-5 h-5 rounded-full flex items-center justify-center font-mono font-bold">
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
              <div className="h-full min-h-0 flex flex-col justify-start md:justify-center items-center text-center px-5 py-1 md:py-6 select-none w-full max-w-4xl mx-auto z-10 relative overflow-y-auto overflow-x-hidden md:overflow-hidden no-scrollbar">
                {/* Backdrop (mandala + veil) clipped to the hero so the oversized
                    absolute layers never inflate the mobile scroll area. */}
                <div className="absolute inset-0 overflow-hidden pointer-events-none" aria-hidden>
                  {/* Big, continuously-morphing mandala (India palette) behind the
                      hero — two layers crossfade to new generated patterns + slow spin.
                      Shown whole (the ornate centre included) with a soft edge-fade. */}
                  <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[min(880px,160%)] aspect-square [mask-image:radial-gradient(circle_at_50%_50%,#000_58%,transparent_78%)] [-webkit-mask-image:radial-gradient(circle_at_50%_50%,#000_58%,transparent_78%)]">
                    {[0, 1].map((layer) => (
                      <Mandala
                        key={layer}
                        seed={mandalaSeeds[layer]}
                        size={880}
                        className={`absolute inset-0 w-full h-full mandala-spin transition-opacity duration-[2200ms] ease-in-out ${
                          mandalaActive === layer ? 'opacity-[0.46]' : 'opacity-0'
                        }`}
                      />
                    ))}
                  </div>
                  {/* Soft ivory veil right behind the text block — lifts the
                      headline/fact off the ornate mandala centre so it reads cleanly. */}
                  <div
                    className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[680px] h-[480px]"
                    style={{
                      background:
                        'radial-gradient(ellipse 54% 58% at 50% 50%, rgba(253,250,243,0.94), rgba(253,250,243,0.66) 50%, transparent 76%)',
                    }}
                  />
                </div>
                <motion.div
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.05, duration: 0.6, ease: 'easeOut' }}
                  className="relative z-10 my-auto flex flex-col items-center"
                >
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

                  {/* One smart slot — heritage fact / contextual nudge / cart. */}
                  <SmartCard
                    slides={heroSlides}
                    ctaDisabled={isThinking}
                    onWrap={() => setFactIdx((prev) => nextFactIndex(prev))}
                  />
                </motion.div>
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
                    <div className="w-8 h-8 brand-orb shrink-0" />
                    <div className="bg-white/80 border border-[#ece3d6]/80 py-2.5 px-4 rounded-full flex items-center gap-2 shadow-xs backdrop-blur-xs">
                      <span className="w-1.5 h-1.5 bg-[#ff8a1e] rounded-full animate-ping" />
                      <span className="text-slate-500 font-medium text-xs italic">
                        {thinkingStatus || 'Thinking…'}
                      </span>
                    </div>
                  </motion.div>
                )}
              </div>
            )}
          </div>

          {/* Bottom composer */}
          <div className="px-4 pt-4 pb-[calc(1rem+var(--safe-bottom))] md:pb-4 bg-gradient-to-t from-[#f7f2ea]/95 via-[#f7f2ea]/30 to-transparent sticky bottom-0 left-0 right-0 z-20">
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
                    className={`shrink-0 flex items-center gap-2 border px-3.5 py-1.5 rounded-[13px] text-xs font-semibold transition-all duration-200 active:scale-95 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 shadow-[0_6px_18px_-14px_rgba(60,40,30,0.4)] hover:shadow-sm ${option.color}`}
                  >
                    <div className="p-0.5 rounded-md shrink-0">
                      <IconComponent className="w-3.5 h-3.5" />
                    </div>
                    <span>{option.label}</span>
                  </button>
                );
              })}
            </div>

            <form
              onSubmit={handleSubmit}
              className="w-full bg-white/[0.86] border border-[#ece3d6]/80 backdrop-blur-md shadow-[0_14px_36px_-22px_rgba(60,40,30,0.45)] rounded-2xl md:rounded-[18px] flex items-center p-1 md:p-1.5 gap-1.5 md:gap-2 focus-within:shadow-[0_18px_42px_-22px_rgba(60,40,30,0.5)] transition-shadow duration-300"
            >
              <button
                type="button"
                tabIndex={-1}
                className="w-8 h-8 md:w-9 md:h-9 text-[#a89f95] hover:text-[#2b2f72] hover:bg-[#2b2f72]/8 rounded-[10px] md:rounded-[11px] flex items-center justify-center transition-colors shrink-0 cursor-pointer"
                title="Attachment menu (coming soon)"
              >
                <Paperclip className="w-4 h-4 md:w-4.5 md:h-4.5" />
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
                placeholder="Ask Companion"
                disabled={isThinking}
                rows={1}
                className="flex-1 bg-transparent border-none outline-none text-[14px] md:text-[14.5px] text-[#221b2e] placeholder-[#a89f95] py-1.5 md:py-2.5 px-1.5 md:px-2 focus:ring-0 min-w-0 resize-none max-h-40 leading-normal md:leading-relaxed"
              />

              <div className="shrink-0 [&_button]:!w-8 [&_button]:!h-8 md:[&_button]:!w-9 md:[&_button]:!h-9">
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
                className={`w-9 h-9 md:w-10 md:h-10 rounded-xl md:rounded-[13px] flex items-center justify-center transition-all shrink-0 cursor-pointer ${
                  inputValue.trim() && !isThinking
                    ? 'btn-ink opacity-100 text-white'
                    : 'bg-[#f3eee6] text-[#bcb3a6]'
                }`}
              >
                <Send className="w-3.5 h-3.5 md:w-4 md:h-4 stroke-[2.2]" />
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
