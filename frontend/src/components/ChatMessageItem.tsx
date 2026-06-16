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
import { ActivityTrail } from './ActivityTrail';
import { LocationPicker } from './LocationPicker';
import type { GeoCoordinate, Message, SessionAddress } from '../types';

interface ChatMessageItemProps {
  msg: Message;
  /** Is this the most recent assistant message? Controls whether the
   *  address-choice chips are eligible to surface. */
  isLatestAssistant?: boolean;
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
  isThinking = false,
  sessionAddresses = [],
  onSelectAddress,
  onQuickPrompt,
  quickPromptDisabled,
  onGrantLocation,
}: ChatMessageItemProps) {
  const isUser = msg.role === 'user';
  const [liked, setLiked] = React.useState<'up' | 'down' | null>(null);

  // Hide avatar + indent only for the grocery category grid, not the intro card.
  const isCardsOnly =
    !isUser
    && !(msg.content || '').trim()
    && msg.attachments?.some((a) => a.kind === 'grocery_categories');

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
      className={`flex ${isUser ? 'justify-end' : isCardsOnly ? 'justify-start -mt-1.5' : 'justify-start gap-3.5'} items-start w-full group`}
    >
      {/* Assistant avatar — hidden for cards-only turns like grocery picker */}
      {!isUser && !isCardsOnly && (
        <div className="brand-orb w-9 h-9 shrink-0 select-none transform hover:scale-105 transition-all relative">
          <span className="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-[#1a9d6b] border-2 border-white z-10" />
        </div>
      )}

      {/* Bubble body + cards + chips. Cards-only turns (voice tool results
          flushed without narration) skip the empty bubble shell. */}
      <div className={`flex flex-col min-w-0 ${isUser ? 'max-w-[75%] items-end' : isCardsOnly ? 'w-full flex-1' : 'max-w-[88%] flex-1'}`}>
        {(msg.content || '').trim() && (
        <div
          className={`animate-fade-in px-3.5 rounded-[16px] text-[13px] leading-snug transition-all duration-200 max-w-full ${
            isUser
              // User bubble: tight pill — small vertical padding so a short
              // message ("hi" / "Order groceries") isn't a tall box.
              ? 'py-1.5 bg-gradient-to-br from-[#2b2f72]/10 via-[#e85d7a]/8 to-[#ff8a1e]/10 border border-[#2b2f72]/15 text-[#221b2e] shadow-sm font-medium rounded-tr-md'
              : 'py-2 bg-white/[0.82] backdrop-blur-sm border border-[#ece3d6]/80 text-[#221b2e] shadow-2xs font-normal rounded-tl-md select-text'
          }`}
        >
          <div
            className={`prose prose-sm max-w-none prose-p:leading-snug ${
              isUser
                // [&_p]:my-0 removes the paragraph margin that was inflating the
                // bubble height vertically.
                ? '[&_p]:my-0 prose-slate prose-strong:text-[#221b2e] prose-a:text-[#2b2f72] prose-code:text-[#c2630f]'
                : 'prose-p:my-1 prose-slate prose-strong:text-[#221b2e] prose-a:text-[#2b2f72] prose-code:text-[#c2630f] prose-pre:bg-slate-900 prose-pre:text-slate-100'
            }`}
          >
            <ReactMarkdown remarkPlugins={[remarkBreaks]}>{msg.content}</ReactMarkdown>
          </div>
        </div>
        )}

        {/* Real attachments (menu_cards / restaurant_cards / cart_card /
            QR images) rendered via the existing component so all the
            previous behaviour (provider theming, quick-prompt popovers,
            "Order now" CTA) is preserved. */}
        {msg.attachments && msg.attachments.length > 0 && (
          <AttachmentList
            attachments={msg.attachments}
            onQuickPrompt={onQuickPrompt}
            quickPromptDisabled={quickPromptDisabled}
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

        {/* Human activity trail — replaces the raw meta chips + "View trace
            details" block with a warm status pill and friendly step rail. */}
        {!isUser && ((msg.meta && msg.meta.length > 0) || (msg.trace && msg.trace.length > 0)) && (
          <ActivityTrail
            meta={msg.meta}
            trace={msg.trace}
            toolsOk={msg.toolsOk}
            toolsError={msg.toolsError}
          />
        )}

        {/* Timestamp + like buttons (assistant only) */}
        <div
          className={`text-[10px] text-slate-400 font-sans font-medium mt-1.5 px-1.5 select-none flex items-center gap-3 ${
            isUser ? 'self-end mr-1' : 'self-start ml-1'
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
                <ThumbsUp size={11} />
              </button>
              <button
                onClick={() => setLiked((prev) => (prev === 'down' ? null : 'down'))}
                className={`p-1 rounded transition-all cursor-pointer hover:text-rose-600 hover:bg-rose-50 ${
                  liked === 'down' ? 'text-rose-600 bg-rose-50/60' : ''
                }`}
                title="Rate unhelpful"
              >
                <ThumbsDown size={11} />
              </button>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}
