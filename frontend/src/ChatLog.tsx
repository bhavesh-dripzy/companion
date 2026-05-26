import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Attachment, MenuCardItem, Message, SessionAddress } from './types';
import { cn } from './utils';
import { Loader2, ExternalLink, Sparkles, MapPin, ChevronDown, Star, UtensilsCrossed } from 'lucide-react';

interface ChatLogProps {
  messages: Message[];
  isThinking: boolean;
  sessionAddresses: SessionAddress[];
  onSelectAddress: (addr: SessionAddress) => void;
}

// Detect when the assistant is prompting the user to choose / switch their
// delivery address. We don't have a structured signal from the backend for
// "awaiting address choice" — the response LLM phrases it in natural prose
// across Hindi, Hinglish, and English — so we keyword-match on the visible
// reply. The picker only renders when the message mentions "address" AND
// contains some choice/switch indicator, so passing references like
// "delivery address" inside a confirmation don't accidentally trigger it.
const ADDRESS_WORD = /\b(address|addresses|adderess|adderesses)\b/i;
const ADDRESS_PROMPT_HINT = /\?|which|select|pick|choose|switch|change|kaunsa|kaunsi|konsa|konsi|batao|prefer|use a different|use my/i;

const looksLikeAddressPrompt = (text: string): boolean => {
  if (!text) return false;
  return ADDRESS_WORD.test(text) && ADDRESS_PROMPT_HINT.test(text);
};

// Compact inline picker rendered under the assistant's bubble whenever it
// asks the user to choose an address. Shows up to 3 chips by default with
// a "+ N more" expand toggle so even users with a long Zomato address
// list can pick without typing. Selecting a chip is dispatched back to
// App.tsx, which fires off a "Switch my delivery address to ..." turn —
// the planner then picks the literal address_id out of session_addresses
// and the executor records the switch in placeholder_values.
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
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-surface border border-amber-500/30 text-xs font-semibold text-amber-300 hover:bg-amber-500/10 hover:border-amber-500/60 transition-all"
          >
            <MapPin size={12} className="text-amber-400" />
            <span className="max-w-[220px] truncate">
              {addr.location_name || addr.address_id}
            </span>
          </button>
        ))}
        {!expanded && remaining > 0 && (
          <button
            onClick={() => setExpanded(true)}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full bg-surface border border-border text-xs font-semibold text-gray-400 hover:text-gray-200 hover:border-border-strong transition-all"
          >
            <ChevronDown size={12} />
            {remaining} more
          </button>
        )}
      </div>
    </div>
  );
};

// Zomato-style menu tile. Renders the dish image (or a fallback when
// Zomato didn't ship one), name, optional description, price (with
// strike-through original when a discount is active), rating chip, and
// a small veg/non-veg/egg dot in the top-left of the image. Designed
// to slot into a two-column responsive grid (single column on mobile)
// matching the rest of the chat bubble's spacing scale.
//
// Read-only by design — the chat is the action layer. The user adds an
// item by saying "add the paneer tikka", which routes through the
// existing planner/executor with the same name they read off the card.
const VEG_DOT_COLOR: Record<NonNullable<MenuCardItem["veg_kind"]>, string> = {
  veg: "bg-green-500 ring-green-700/60",
  non_veg: "bg-red-500 ring-red-700/60",
  egg: "bg-amber-400 ring-amber-700/60",
};

const formatPriceINR = (value: number | undefined): string | null => {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return null;
  }
  // Whole rupees only — Zomato prices are always integral and decimals
  // make the card look "off" against the official UI.
  return `₹${Math.round(value)}`;
};

const MenuCard: React.FC<{ item: MenuCardItem }> = ({ item }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const price = formatPriceINR(item.price);
  const disc = formatPriceINR(item.discounted_price);
  const hasDiscount = !!disc && !!price && disc !== price;
  const showRating = typeof item.rating === "number" && item.rating > 0;
  const showImage = !!item.image_url && !imageBroken;

  return (
    <div className="flex flex-col gap-2 p-3 bg-surface border border-border rounded-2xl shadow-md shadow-black/30 transition-colors hover:border-border-strong">
      <div className="relative w-full aspect-[4/3] rounded-xl overflow-hidden bg-black/40 border border-border">
        {showImage ? (
          <img
            src={item.image_url}
            alt={item.name}
            loading="lazy"
            onError={() => setImageBroken(true)}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-gray-500">
            <UtensilsCrossed size={28} strokeWidth={1.5} />
          </div>
        )}
        {item.veg_kind && (
          <div className="absolute top-2 left-2 w-4 h-4 rounded-sm bg-black/40 backdrop-blur-sm flex items-center justify-center ring-1 ring-white/20">
            <span
              className={cn(
                "block w-2 h-2 rounded-full ring-1",
                VEG_DOT_COLOR[item.veg_kind]
              )}
              aria-label={item.veg_kind === "non_veg" ? "Non-vegetarian" : item.veg_kind === "egg" ? "Contains egg" : "Vegetarian"}
            />
          </div>
        )}
        {showRating && (
          <div className="absolute bottom-2 right-2 inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-black/65 backdrop-blur-sm text-[11px] font-semibold text-amber-300">
            <Star size={10} className="fill-amber-300 text-amber-300" />
            {item.rating!.toFixed(1)}
            {typeof item.votes === "number" && item.votes > 0 && (
              <span className="text-[10px] text-gray-300 font-medium">({item.votes})</span>
            )}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-1 min-w-0">
        <div className="text-sm font-semibold text-gray-100 leading-snug line-clamp-2">
          {item.name}
        </div>
        {item.description && (
          <div className="text-[11px] text-gray-400 leading-snug line-clamp-2">
            {item.description}
          </div>
        )}
        <div className="flex items-baseline gap-2 mt-1">
          {hasDiscount ? (
            <>
              <span className="text-sm font-bold text-amber-300">{disc}</span>
              <span className="text-[11px] text-gray-500 line-through">{price}</span>
            </>
          ) : (
            price && <span className="text-sm font-bold text-amber-300">{price}</span>
          )}
        </div>
      </div>
    </div>
  );
};

const MenuCardGrid: React.FC<{ attachment: Attachment }> = ({ attachment }) => {
  const items = Array.isArray(attachment.items) ? attachment.items : [];
  if (items.length === 0) return null;
  const heading = attachment.restaurant_name
    ? `Menu — ${attachment.restaurant_name}`
    : "Menu";
  const subhead =
    Array.isArray(attachment.categories) && attachment.categories.length > 0
      ? attachment.categories.join(" • ")
      : null;

  return (
    <div className="mt-1 w-full max-w-2xl">
      <div className="flex items-baseline justify-between mb-2 px-1">
        <div className="text-xs font-bold uppercase tracking-wider text-gray-400">
          {heading}
        </div>
        {subhead && (
          <div className="text-[10px] text-gray-500 truncate ml-3 max-w-[55%]">
            {subhead}
          </div>
        )}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {items.map((item, idx) => (
          <MenuCard key={item.variant_id || item.item_id || `${item.name}-${idx}`} item={item} />
        ))}
      </div>
    </div>
  );
};

// Render the structured attachments the backend ships alongside
// `assistant_message` — payment QR (kind=image, mime_type=image/png,
// data_b64=...) or menu_cards (kind=menu_cards, items=[...]). Every
// new attachment kind the executor emits should add a branch below.
const AttachmentList: React.FC<{ attachments: Attachment[] }> = ({ attachments }) => {
  const cards = attachments
    .filter((att): att is Attachment => !!att && typeof att === 'object')
    .map((att, idx) => {
      const kind = String(att.kind || '').toLowerCase();

      // Menu card grid lives in its own component; the rest of this
      // function handles image/QR/UPI-style attachments only.
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
          className="flex flex-col items-start gap-2 p-3 bg-surface border border-border rounded-2xl max-w-[280px] shadow-lg shadow-black/40"
        >
          {hasImage && (
            <img
              src={`data:${mime};base64,${b64}`}
              alt={String(att.alt || 'attachment')}
              className="w-56 h-56 block bg-white border border-border rounded-xl p-2"
              style={{ imageRendering: 'pixelated' }}
            />
          )}
          {showOversizeFallback && (
            <div className="text-xs text-gray-400">
              QR is too large to inline — please use the UPI link.
            </div>
          )}
          {caption && (
            <div className="text-xs text-gray-400 leading-snug">{caption}</div>
          )}
          {showUpi && (
            <a
              href={upi}
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-accent text-gray-900 text-xs font-bold hover:bg-accent-hover transition-colors shadow-md shadow-amber-500/20"
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

export const ChatLog: React.FC<ChatLogProps> = ({
  messages,
  isThinking,
  sessionAddresses,
  onSelectAddress,
}) => {
  // Locate the latest assistant message — that's the only one allowed to
  // surface the address-choice chips. Older assistant turns where the user
  // already replied stay clean (otherwise the chat would be peppered with
  // stale pickers).
  let lastAssistantIdx = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'assistant') {
      lastAssistantIdx = i;
      break;
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-7">
      {messages.map((msg, i) => (
        <div
          key={i}
          className={cn(
            "flex items-end gap-3 max-w-4xl mx-auto",
            msg.role === 'user' ? "flex-row-reverse" : "flex-row"
          )}
        >
          {/* Avatar — only on the assistant side, matching the reference.
              User messages right-align without an avatar so the amber
              bubble itself is the focal point. */}
          {msg.role === 'assistant' ? (
            <div className="w-8 h-8 rounded-full brand-gradient text-gray-900 flex items-center justify-center flex-shrink-0 shadow-md shadow-amber-500/30 mb-1">
              <Sparkles size={14} strokeWidth={2.5} />
            </div>
          ) : (
            <div className="w-1 flex-shrink-0" />
          )}

          <div className={cn(
            "flex flex-col gap-2 min-w-0",
            msg.role === 'user' ? "items-end" : "items-start"
          )}>
            <div className={cn(
              "px-5 py-3 text-[15px] leading-relaxed max-w-full",
              msg.role === 'user'
                ? "bg-accent text-gray-900 rounded-3xl rounded-br-md shadow-lg shadow-amber-500/20"
                : "bg-surface border border-border text-gray-100 rounded-3xl rounded-bl-md shadow-md shadow-black/30"
            )}>
              <div className={cn(
                "prose max-w-none prose-p:leading-relaxed prose-p:my-1.5",
                msg.role === 'user'
                  ? "prose-gray prose-strong:text-gray-900 prose-a:text-gray-900 prose-a:underline prose-code:text-gray-900"
                  : "prose-invert prose-pre:bg-black/50 prose-pre:border prose-pre:border-border prose-code:text-amber-300 prose-strong:text-white prose-a:text-amber-300"
              )}>
                <ReactMarkdown>
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
                  <span key={idx} className="text-[10px] font-bold uppercase tracking-wider px-2 py-1 bg-surface border border-border rounded-md text-gray-400">
                    {m}
                  </span>
                ))}
              </div>
            )}

            {msg.trace && msg.trace.length > 0 && (
              <details className="mt-2 w-full">
                <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-300 transition-colors font-medium">
                  View trace details
                </summary>
                <div className="mt-2 space-y-2 p-3 bg-black/30 rounded-xl border border-border">
                  {msg.trace.map((t, idx) => (
                    <div key={idx} className="text-[11px] grid grid-cols-[80px_1fr] gap-2">
                      <span className="text-gray-300 font-semibold">{t.provider || t.stage}:</span>
                      <span className="text-gray-500 truncate">{t.detail}</span>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        </div>
      ))}

      {isThinking && (
        <div className="flex items-end gap-3 max-w-4xl mx-auto">
          <div className="w-8 h-8 rounded-full brand-gradient text-gray-900 flex items-center justify-center flex-shrink-0 shadow-md shadow-amber-500/30">
            <Sparkles size={14} strokeWidth={2.5} />
          </div>
          <div className="flex items-center gap-2 px-1 py-2">
            <Loader2 size={16} className="animate-spin text-amber-400" />
            <span className="text-sm text-gray-400 font-medium">Analyzing, please wait…</span>
          </div>
        </div>
      )}
    </div>
  );
};
