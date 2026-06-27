/**
 * Companion chat message bubble (redesigned shell + existing backend
 * payload). Renders one assistant/user turn from the real `Message`
 * shape (markdown body + optional attachments + optional trace + meta
 * chips) inside the new UI's avatar/bubble layout. Reuses the existing
 * `AttachmentList` + `AddressChoices` renderers from `ChatLog.tsx` so
 * Zomato/Swiggy menu, restaurant, and cart cards keep working exactly
 * as they did under the old skin.
 */

import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import { motion } from 'motion/react';
import { ThumbsDown, ThumbsUp } from 'lucide-react';
import { AddressChoices, AttachmentList, looksLikeAddressPrompt } from '../ChatLog';
import { AgentCards } from './AgentCards';
import { LocationPicker } from './LocationPicker';
import { StreamingTurn } from './StreamingTurn';
import type { GeoCoordinate, Message, SessionAddress } from '../types';

interface ChatMessageItemProps {
  msg: Message;
  /** Is this the most recent assistant message? Controls whether the
   *  address-choice chips are eligible to surface. */
  isLatestAssistant?: boolean;
  /** Is this the LAST message of a consecutive same-role run? Only the tail shows the
   *  timestamp/footer, so a multi-message turn (grocery intro + category card) stamps once. */
  isGroupTail?: boolean;
  /** Hide the address chips while the next turn is in flight so the
   *  user doesn't fire two address-switch requests on top of each
   *  other. */
  isThinking?: boolean;
  /** Saved delivery addresses to render under the bubble when the
   *  assistant is explicitly asking the user to pick one. */
  sessionAddresses?: SessionAddress[];
  onSelectAddress?: (addr: SessionAddress) => void;
  /** Forwarded into AttachmentList so restaurant/menu/cart quick
   *  actions can fire follow-up prompts straight back through the
   *  agent (e.g. "Add Margherita Pizza to my Zomato cart"). */
  onQuickPrompt?: (prompt: string) => void;
  quickPromptDisabled?: boolean;
  /** Called with geocoded coordinates when the user submits the location picker. */
  onGrantLocation?: (geo: GeoCoordinate) => void;
}

const formatBubbleTimestamp = (iso?: string): string => {
  // Backend doesn't always ship a per-message timestamp (older rows in
  // Dynamo, in-flight bubbles before persist round-trips, etc.) so
  // fall back to "now" — matches the previous ChatLog behaviour.
  const d = iso ? new Date(iso) : new Date();
  if (Number.isNaN(d.getTime())) return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

export function ChatMessageItem({
  msg,
  isLatestAssistant = false,
  isGroupTail = true,
  isThinking = false,
  sessionAddresses = [],
  onSelectAddress,
  onQuickPrompt,
  quickPromptDisabled,
  onGrantLocation,
}: ChatMessageItemProps) {
  const isUser = msg.role === 'user';
  const [liked, setLiked] = React.useState<'up' | 'down' | null>(null);

  // Hide avatar + indent only for the category grids (grocery / District), not
  // the intro card.
  const isCardsOnly =
    !isUser
    && !(msg.content || '').trim()
    && msg.attachments?.some(
      (a) => a.kind === 'grocery_categories' || a.kind === 'district_categories',
    );

  // Search-result grids get the FULL content width (no avatar, no 88% bubble cap) so the provider
  // block + product tiles span edge-to-edge from the left, like a store's results page. Checks both
  // the finalized attachments AND the live stream segments, so the width doesn't jump at stream end.
  const isGrid = (a?: { kind?: string }) =>
    a?.kind === 'instamart_product_cards' || a?.kind === 'zepto_product_cards';
  const hasProductGrid =
    !isUser
    && ((msg.attachments?.some(isGrid) ?? false)
      || (msg.segments?.some((s) => s.kind === 'cards' && s.attachments?.some(isGrid)) ?? false));

  // Does this assistant turn carry any narration text? Product-grid turns keep the
  // grid edge-to-edge (extreme left) but still get the Omi avatar + an indented
  // narration line when there's something to say.
  const hasText =
    (msg.content || '').trim().length > 0
    || (msg.segments?.some((s) => s.kind === 'text' && (s.text || '').trim().length > 0) ?? false);
  const gridWithNarration = hasProductGrid && hasText;

  const showAddressPicker =
    !isUser
    && isLatestAssistant
    && !isThinking
    && sessionAddresses.length > 0
    && looksLikeAddressPrompt(msg.content || '');

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: 'easeOut' }}
      className={`flex ${isUser ? 'justify-end' : isCardsOnly || hasProductGrid ? 'justify-start' : 'justify-start gap-2.5'} items-start w-full group`}
    >
      {/* Assistant avatar (Omi) — inline beside the bubble for normal turns. Product-grid turns hide
          it here (so the grid spans full width) and instead attach an avatar to the narrator reply
          itself (StreamingTurn / the static narration block below). Cards-only turns: no avatar. */}
      {!isUser && !isCardsOnly && !hasProductGrid && (
        <div className="w-8 h-8 shrink-0 select-none relative transform hover:scale-105 transition-all">
          <img
            src="/omi-logo.png"
            alt="Omi"
            draggable={false}
            className="w-8 h-8 rounded-full object-cover bg-white shadow-sm ring-1 ring-[#ece3d6]/80"
          />
          <span className="absolute bottom-0 right-0 w-2 h-2 rounded-full bg-[#1a9d6b] border-2 border-white z-10" />
        </div>
      )}

      {/* Bubble body + cards + chips. Cards-only turns (voice tool results
          flushed without narration) skip the empty bubble shell. */}
      <div className={`flex flex-col min-w-0 ${isUser ? 'max-w-[75%] items-end' : isCardsOnly || hasProductGrid ? 'w-full flex-1' : 'max-w-[88%] flex-1'}`}>
        {/* A LIVE/streamed turn plays through the generic player (typed narration + cards build in,
            in arrival order). A normal/reloaded turn renders the static bubble + attachments below. */}
        {msg.segments && (msg.segments.length > 0 || msg.streaming) ? (
          <StreamingTurn
            segments={msg.segments}
            streaming={!!msg.streaming}
            working={msg.working}
            onQuickPrompt={onQuickPrompt}
            quickPromptDisabled={quickPromptDisabled}
            responseAvatar={gridWithNarration}
          />
        ) : (
          <>
        {(msg.content || '').trim() && (
        <div className={gridWithNarration ? 'flex items-start gap-2.5' : ''}>
          {/* Grid turns: the narrator reply carries its own Omi avatar (the grid above is full-width). */}
          {gridWithNarration && (
            <div className="w-8 h-8 shrink-0 select-none relative">
              <img
                src="/omi-logo.png"
                alt="Omi"
                draggable={false}
                className="w-9 h-9 rounded-full object-cover bg-white shadow-sm ring-1 ring-[#ece3d6]/80"
              />
              <span className="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-[#1a9d6b] border-2 border-white z-10" />
            </div>
          )}
          <div
            className={`animate-fade-in px-3.5 rounded-[15px] leading-snug transition-all duration-200 max-w-full ${
              gridWithNarration ? 'w-fit' : ''
            } ${
              isUser
                ? 'text-[13.5px] py-2.5 bg-gradient-to-br from-[#2b2f72]/10 via-[#e85d7a]/8 to-[#ff8a1e]/10 border border-[#2b2f72]/15 text-[#221b2e] shadow-sm font-medium rounded-tr-md'
                : 'text-[13.5px] py-2.5 bg-white/[0.82] backdrop-blur-sm border border-[#ece3d6]/80 text-[#221b2e] shadow-2xs font-normal rounded-tl-md select-text'
            }`}
          >
            <div
              className={`prose prose-sm max-w-none prose-p:leading-snug ${
                isUser
                  // [&_p]:my-0 removes the paragraph margin that was inflating the
                  // bubble height vertically.
                  ? '[&_p]:my-0 prose-p:text-[13.5px] prose-li:text-[13.5px] prose-slate prose-strong:text-[#221b2e] prose-a:text-[#2b2f72] prose-code:text-[#c2630f]'
                  : 'prose-p:my-1 prose-p:text-[13.5px] prose-li:text-[13.5px] prose-slate prose-strong:text-[#221b2e] prose-a:text-[#2b2f72] prose-code:text-[#c2630f] prose-pre:bg-slate-900 prose-pre:text-slate-100'
              }`}
            >
              <ReactMarkdown remarkPlugins={[remarkBreaks]}>{msg.content}</ReactMarkdown>
            </div>
          </div>
        </div>
        )}

        {/* Real attachments (menu_cards / restaurant_cards / cart_card /
            QR images) rendered via the existing component so all the
            previous behaviour (provider theming, quick-prompt popovers,
            "Order now" CTA) is preserved. */}
        {msg.attachments && msg.attachments.length > 0 && (
          // For a content-LESS card message (e.g. the grocery category picker), cancel
          // AttachmentList's own mt-1 so the inter-message gap matches a plain text turn. Messages
          // WITH narration keep the mt-1 as the separator between the bubble and the cards.
          <div className={(msg.content || '').trim() ? '' : '-mt-3'}>
            <AttachmentList
              attachments={msg.attachments}
              onQuickPrompt={onQuickPrompt}
              quickPromptDisabled={quickPromptDisabled}
            />
          </div>
        )}
          </>
        )}

        {/* New agent backend renders products/cart via the EXISTING attachment
            cards above (instamart_product_cards / instamart_cart_card / zepto_*),
            so the polished old UI + address bar are reused as-is. The ONE extra
            thing it adds is a pending disambiguation: when a cart op partially
            resolved (some items added, some genuinely ambiguous), show tappable
            choice chips so the user picks the exact variant in one tap. Latest
            assistant turn only, so stale chips don't linger after the next turn. */}
        {!isUser && isLatestAssistant && !isThinking && msg.pendingAction?.type === 'disambiguate' && (
          <AgentCards
            pending={msg.pendingAction}
            onAction={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        )}

        {/* Address picker — only on the latest assistant turn, and only
            when the body actually asks the user to pick one. */}
        {showAddressPicker && onSelectAddress && (
          <AddressChoices addresses={sessionAddresses} onSelect={onSelectAddress} />
        )}

        {/* Inline location picker — shown when Zepto needed GPS but it was unavailable */}
        {!isUser && msg.askLocation && isLatestAssistant && !isThinking && onGrantLocation && (
          <LocationPicker onConfirm={onGrantLocation} />
        )}

        {/* Timestamp + like buttons (assistant only). Only the tail of a consecutive same-role run shows
            it, so a multi-message turn (grocery intro + category card) stamps once, under the last message.
            Grid turns indent past the inline avatar (w-8 + gap-2.5 ≈ 42px) so the timestamp lines up under
            the narration bubble, not the logo. */}
        {isGroupTail && (
        <div
          className={`text-[9px] text-slate-400 font-sans font-medium mt-1 px-1.5 select-none flex items-center gap-3 ${
            isUser ? 'self-end mr-1' : hasProductGrid ? 'self-start ml-[42px]' : 'self-start ml-1'
          }`}
        >
          <span>{formatBubbleTimestamp(msg.created_at)}</span>
          {!isUser && (
            <div className="flex items-center gap-1">
              <button
                onClick={() => setLiked((prev) => (prev === 'up' ? null : 'up'))}
                className={`p-1 rounded transition-all cursor-pointer hover:text-emerald-600 hover:bg-emerald-50 ${
                  liked === 'up' ? 'text-emerald-600 bg-emerald-50/60' : ''
                }`}
                title="Rate helpful"
              >
                <ThumbsUp size={10} />
              </button>
              <button
                onClick={() => setLiked((prev) => (prev === 'down' ? null : 'down'))}
                className={`p-1 rounded transition-all cursor-pointer hover:text-rose-600 hover:bg-rose-50 ${
                  liked === 'down' ? 'text-rose-600 bg-rose-50/60' : ''
                }`}
                title="Rate unhelpful"
              >
                <ThumbsDown size={10} />
              </button>
            </div>
          )}
        </div>
        )}
      </div>
    </motion.div>
  );
}
