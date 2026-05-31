import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import type { Attachment, MenuCardItem, Message, SessionAddress } from './types';
import { cn } from './utils';
import {
  ExternalLink,
  MapPin,
  ChevronDown,
  Star,
  UtensilsCrossed,
  RefreshCw,
  ThumbsUp,
  ThumbsDown,
} from 'lucide-react';

interface ChatLogProps {
  messages: Message[];
  isThinking: boolean;
  sessionAddresses: SessionAddress[];
  onSelectAddress: (addr: SessionAddress) => void;
  searchQuery: string;
}

// Detect when the assistant is prompting the user to choose / switch their
// delivery address. Same keyword heuristic the previous skin used — the
// backend doesn't ship a structured signal for "awaiting address choice".
const ADDRESS_WORD = /\b(address|addresses|adderess|adderesses)\b/i;
const ADDRESS_PROMPT_HINT = /\?|which|select|pick|choose|switch|change|kaunsa|kaunsi|konsa|konsi|batao|prefer|use a different|use my/i;

const looksLikeAddressPrompt = (text: string): boolean => {
  if (!text) return false;
  return ADDRESS_WORD.test(text) && ADDRESS_PROMPT_HINT.test(text);
};

const AddressChoices: React.FC<{
  addresses: SessionAddress[];
  onSelect: (addr: SessionAddress) => void;
}> = ({ addresses, onSelect }) => {
  const [expanded, setExpanded] = useState(false);

  const valid = addresses
    .filter((a): a is SessionAddress => !!a && typeof a === 'object')
    .filter((a) => !!(a.location_name || a.address_id));

  if (valid.length === 0) return null;

  const visible = expanded ? valid : valid.slice(0, 3);
  const remaining = valid.length - visible.length;

  return (
    <div className="mt-2 flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {visible.map((addr, idx) => (
          <button
            key={addr.address_id || idx}
            onClick={() => onSelect(addr)}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-50 hover:border-slate-300 shadow-sm transition-all"
          >
            <MapPin size={12} className="text-slate-500" />
            <span className="max-w-[220px] truncate">{addr.location_name || addr.address_id}</span>
          </button>
        ))}
        {!expanded && remaining > 0 && (
          <button
            onClick={() => setExpanded(true)}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full bg-slate-100/70 border border-slate-200 text-xs font-semibold text-slate-500 hover:text-slate-800 hover:border-slate-300 transition-all"
          >
            <ChevronDown size={12} />
            {remaining} more
          </button>
        )}
      </div>
    </div>
  );
};

const VEG_DOT_COLOR: Record<NonNullable<MenuCardItem['veg_kind']>, string> = {
  veg: 'bg-green-500 ring-green-700/40',
  non_veg: 'bg-red-500 ring-red-700/40',
  egg: 'bg-amber-400 ring-amber-700/40',
};

const formatPriceINR = (value: number | undefined): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null;
  return `₹${Math.round(value)}`;
};

const MenuCard: React.FC<{ item: MenuCardItem }> = ({ item }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const price = formatPriceINR(item.price);
  const disc = formatPriceINR(item.discounted_price);
  const hasDiscount = !!disc && !!price && disc !== price;
  const showRating = typeof item.rating === 'number' && item.rating > 0;
  const showImage = !!item.image_url && !imageBroken;

  return (
    <div className="snap-start shrink-0 w-40 flex flex-col gap-1.5 p-2 bg-white/90 border border-slate-200/80 rounded-xl shadow-sm hover:shadow-md hover:border-slate-300 transition-all">
      <div className="relative w-full aspect-square rounded-lg overflow-hidden bg-slate-100 border border-slate-200/70">
        {showImage ? (
          <img
            src={item.image_url}
            alt={item.name}
            loading="lazy"
            onError={() => setImageBroken(true)}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-slate-400">
            <UtensilsCrossed size={22} strokeWidth={1.5} />
          </div>
        )}
        {item.veg_kind && (
          <div className="absolute top-1.5 left-1.5 w-3.5 h-3.5 rounded-sm bg-white/90 backdrop-blur-sm flex items-center justify-center ring-1 ring-slate-200">
            <span
              className={cn('block w-1.5 h-1.5 rounded-full ring-1', VEG_DOT_COLOR[item.veg_kind])}
              aria-label={item.veg_kind === 'non_veg' ? 'Non-vegetarian' : item.veg_kind === 'egg' ? 'Contains egg' : 'Vegetarian'}
            />
          </div>
        )}
        {showRating && (
          <div className="absolute bottom-1.5 right-1.5 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md bg-white/90 backdrop-blur-sm text-[10px] font-semibold text-slate-800 border border-slate-200/70">
            <Star size={9} className="fill-amber-400 text-amber-500" />
            {item.rating!.toFixed(1)}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-0.5 min-w-0">
        <div className="text-[12px] font-semibold text-slate-900 leading-snug line-clamp-2">{item.name}</div>
        <div className="flex items-baseline gap-1.5 mt-0.5">
          {hasDiscount ? (
            <>
              <span className="text-[13px] font-bold text-slate-900">{disc}</span>
              <span className="text-[10px] text-slate-400 line-through">{price}</span>
            </>
          ) : (
            price && <span className="text-[13px] font-bold text-slate-900">{price}</span>
          )}
        </div>
      </div>
    </div>
  );
};

const MenuCardGrid: React.FC<{ attachment: Attachment }> = ({ attachment }) => {
  const items = Array.isArray(attachment.items) ? attachment.items : [];
  if (items.length === 0) return null;
  const heading = attachment.restaurant_name ? `Menu — ${attachment.restaurant_name}` : 'Menu';
  const subhead =
    Array.isArray(attachment.categories) && attachment.categories.length > 0
      ? attachment.categories.join(' • ')
      : null;

  return (
    <div className="mt-1 w-full max-w-full">
      <div className="flex items-baseline justify-between mb-2 px-1">
        <div className="text-xs font-bold uppercase tracking-wider text-slate-500">{heading}</div>
        {subhead && (
          <div className="text-[10px] text-slate-400 truncate ml-3 max-w-[55%]">{subhead}</div>
        )}
      </div>
      <div
        className="flex flex-row gap-2.5 overflow-x-auto snap-x snap-mandatory pb-2 -mx-1 px-1 [scrollbar-width:thin] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-thumb]:bg-slate-300 [&::-webkit-scrollbar-thumb]:rounded-full"
      >
        {items.map((item, idx) => (
          <MenuCard key={item.variant_id || item.item_id || `${item.name}-${idx}`} item={item} />
        ))}
      </div>
    </div>
  );
};

const AttachmentList: React.FC<{ attachments: Attachment[] }> = ({ attachments }) => {
  const cards = attachments
    .filter((att): att is Attachment => !!att && typeof att === 'object')
    .map((att, idx) => {
      const kind = String(att.kind || '').toLowerCase();
      if (kind === 'menu_cards') {
        return <MenuCardGrid key={`menu-${idx}`} attachment={att} />;
      }
      const mime = String(att.mime_type || 'image/png');
      const b64 = typeof att.data_b64 === 'string' ? att.data_b64 : '';
      const caption = att.caption ? String(att.caption) : '';
      const upi = typeof att.upi_link === 'string' ? att.upi_link.trim() : '';
      const showUpi = upi.toLowerCase().startsWith('upi://');
      const hasImage = kind === 'image' && b64 && mime.startsWith('image/');
      const showOversizeFallback = !hasImage && att.oversize === true;

      if (!hasImage && !showOversizeFallback && !caption && !showUpi) return null;

      return (
        <div
          key={idx}
          className="flex flex-col items-start gap-2 p-3 bg-white/90 border border-slate-200/80 rounded-2xl max-w-[280px] shadow-sm"
        >
          {hasImage && (
            <img
              src={`data:${mime};base64,${b64}`}
              alt={String(att.alt || 'attachment')}
              className="w-56 h-56 block bg-white border border-slate-200 rounded-xl p-2"
              style={{ imageRendering: 'pixelated' }}
            />
          )}
          {showOversizeFallback && (
            <div className="text-xs text-slate-500">
              QR is too large to inline — please use the UPI link.
            </div>
          )}
          {caption && <div className="text-xs text-slate-500 leading-snug">{caption}</div>}
          {showUpi && (
            <a
              href={upi}
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-slate-900 hover:bg-slate-800 text-white text-xs font-bold transition-colors shadow-sm"
            >
              <ExternalLink size={12} />
              Open in UPI app{att.amount_inr ? ` ₹${att.amount_inr}` : ''}
            </a>
          )}
        </div>
      );
    })
    .filter(Boolean);

  if (cards.length === 0) return null;
  return <div className="flex flex-col gap-3 mt-1">{cards}</div>;
};

const formatTimestamp = (): string => {
  // Backend doesn't ship a timestamp on Message; we just show the
  // user's local hour:minute when the bubble renders so the chat has
  // a subtle time anchor like the reference comp.
  const now = new Date();
  return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

export const ChatLog: React.FC<ChatLogProps> = ({
  messages,
  isThinking,
  sessionAddresses,
  onSelectAddress,
  searchQuery,
}) => {
  const endRef = useRef<HTMLDivElement | null>(null);
  const [likedMessages, setLikedMessages] = useState<Record<number, 'up' | 'down' | null>>({});

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  // Locate the latest assistant message — that's the only one allowed to
  // surface the address-choice chips.
  let lastAssistantIdx = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'assistant') {
      lastAssistantIdx = i;
      break;
    }
  }

  const q = searchQuery.trim().toLowerCase();
  const visibleMessages = q
    ? messages.filter((m) => (m.content || '').toLowerCase().includes(q))
    : messages;

  const handleLike = (idx: number, action: 'up' | 'down') => {
    setLikedMessages((prev) => ({ ...prev, [idx]: prev[idx] === action ? null : action }));
  };

  return (
    <div className="flex-1 overflow-y-auto px-3 md:px-6 py-5 md:py-6 space-y-6 md:space-y-7 scroll-smooth pb-36 md:pb-40">
      {q && visibleMessages.length === 0 && (
        <div className="text-center text-xs text-slate-500 italic">
          No messages match “{searchQuery}”.
        </div>
      )}

      {visibleMessages.map((msg) => {
        const i = messages.indexOf(msg);
        const isUser = msg.role === 'user';
        const liked = likedMessages[i] ?? null;

        return (
          <div
            key={i}
            className={cn('flex flex-col space-y-1.5', isUser ? 'items-end' : 'items-start')}
          >
            <div className={cn('flex gap-2.5 md:gap-3 max-w-[92%] md:max-w-[85%]', isUser ? 'flex-row-reverse' : 'flex-row')}>
              {!isUser && (
                <div
                  className="w-8 h-8 rounded-full bg-slate-950 text-white flex items-center justify-center font-bold relative shrink-0 text-[10px] shadow"
                  title="Companion"
                >
                  C
                  <span className="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-emerald-500 border-2 border-white" />
                </div>
              )}

              <div className="flex flex-col gap-1 min-w-0">
                <div
                  className={cn(
                    'py-3 px-5 rounded-3xl text-sm leading-relaxed shadow-sm max-w-full',
                    isUser
                      ? 'bg-slate-950 text-white rounded-tr-none font-medium'
                      : 'bg-white/90 text-slate-900 border border-slate-200/70 rounded-tl-none font-normal backdrop-blur-sm',
                  )}
                >
                  <div
                    className={cn(
                      'prose max-w-none prose-p:leading-relaxed prose-p:my-1.5',
                      isUser
                        ? 'prose-invert prose-strong:text-white prose-a:text-amber-300 prose-code:text-amber-200'
                        : 'prose-slate prose-strong:text-slate-900 prose-a:text-blue-600 prose-code:text-rose-600 prose-pre:bg-slate-900 prose-pre:text-slate-100',
                    )}
                  >
                    <ReactMarkdown remarkPlugins={[remarkBreaks]}>
                      {msg.content}
                    </ReactMarkdown>
                  </div>
                </div>

                {msg.attachments && msg.attachments.length > 0 && (
                  <AttachmentList attachments={msg.attachments} />
                )}

                {msg.role === 'assistant'
                  && i === lastAssistantIdx
                  && !isThinking
                  && sessionAddresses.length > 0
                  && looksLikeAddressPrompt(msg.content) && (
                    <AddressChoices addresses={sessionAddresses} onSelect={onSelectAddress} />
                  )}

                {msg.meta && msg.meta.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-1">
                    {msg.meta.map((m, idx) => (
                      <span
                        key={idx}
                        className="text-[10px] font-bold uppercase tracking-wider px-2 py-1 bg-white/70 border border-slate-200/70 rounded-md text-slate-500"
                      >
                        {m}
                      </span>
                    ))}
                  </div>
                )}

                {msg.trace && msg.trace.length > 0 && (
                  <details className="mt-2 w-full">
                    <summary className="text-xs text-slate-400 cursor-pointer hover:text-slate-700 transition-colors font-medium">
                      View trace details
                    </summary>
                    <div className="mt-2 space-y-2 p-3 bg-white/70 rounded-xl border border-slate-200/70">
                      {msg.trace.map((t: any, idx: number) => (
                        <div key={idx} className="text-[11px] grid grid-cols-[80px_1fr] gap-2">
                          <span className="text-slate-700 font-semibold">
                            {t.provider || t.stage}:
                          </span>
                          <span className="text-slate-500 truncate">{t.detail}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            </div>

            <div
              className={cn(
                'text-[10px] text-slate-400/90 px-2.5 flex items-center gap-3 select-none',
                isUser ? 'justify-end mr-2' : 'justify-start ml-11',
              )}
            >
              <span>{formatTimestamp()}</span>

              {!isUser && (
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => handleLike(i, 'up')}
                    className={cn(
                      'p-1 rounded transition-all cursor-pointer hover:text-emerald-600 hover:bg-emerald-50',
                      liked === 'up' && 'text-emerald-600 bg-emerald-50/60',
                    )}
                    title="Rate helpful"
                  >
                    <ThumbsUp size={11} />
                  </button>
                  <button
                    onClick={() => handleLike(i, 'down')}
                    className={cn(
                      'p-1 rounded transition-all cursor-pointer hover:text-rose-600 hover:bg-rose-50',
                      liked === 'down' && 'text-rose-600 bg-rose-50/60',
                    )}
                    title="Rate unhelpful"
                  >
                    <ThumbsDown size={11} />
                  </button>
                </div>
              )}
            </div>
          </div>
        );
      })}

      {isThinking && (
        <div className="flex items-start gap-3 select-none">
          <div className="w-8 h-8 rounded-full bg-slate-950 text-white flex items-center justify-center font-bold text-[10px] shadow">
            C
          </div>
          <div className="bg-white/95 border border-slate-200/70 py-3 px-5 rounded-3xl rounded-tl-none font-medium text-slate-500 text-xs flex items-center gap-2 shadow-sm">
            <RefreshCw size={13} className="animate-spin text-slate-400" />
            <span>Thinking…</span>
          </div>
        </div>
      )}

      <div ref={endRef} />
    </div>
  );
};
