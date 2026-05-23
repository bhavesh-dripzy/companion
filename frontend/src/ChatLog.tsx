import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Attachment, Message, SessionAddress } from './types';
import { cn } from './utils';
import { Loader2, ExternalLink, Sparkles, MapPin, ChevronDown } from 'lucide-react';

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
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-violet-200 text-xs font-semibold text-violet-700 hover:bg-violet-50 hover:border-violet-300 transition-all shadow-sm"
          >
            <MapPin size={12} className="text-violet-500" />
            <span className="max-w-[220px] truncate">
              {addr.location_name || addr.address_id}
            </span>
          </button>
        ))}
        {!expanded && remaining > 0 && (
          <button
            onClick={() => setExpanded(true)}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full bg-gray-50 border border-border text-xs font-semibold text-gray-600 hover:bg-gray-100 transition-all"
          >
            <ChevronDown size={12} />
            {remaining} more
          </button>
        )}
      </div>
    </div>
  );
};

// Render the structured attachments the backend ships alongside
// `assistant_message` — currently the Zomato checkout QR (kind=image,
// mime_type=image/png, data_b64=...). The contract matches the Django
// template's renderAttachments() so any new attachment kinds the
// executor emits surface in both UIs.
const AttachmentList: React.FC<{ attachments: Attachment[] }> = ({ attachments }) => {
  const cards = attachments
    .filter((att): att is Attachment => !!att && typeof att === 'object')
    .map((att, idx) => {
      const kind = String(att.kind || '').toLowerCase();
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
          className="flex flex-col items-start gap-2 p-3 bg-white border border-border rounded-2xl max-w-[280px] shadow-[0_4px_20px_-8px_rgba(99,102,241,0.18)]"
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
            <div className="text-xs text-gray-600">
              QR is too large to inline — please use the UPI link.
            </div>
          )}
          {caption && (
            <div className="text-xs text-gray-600 leading-snug">{caption}</div>
          )}
          {showUpi && (
            <a
              href={upi}
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full brand-gradient text-white text-xs font-bold hover:opacity-90 transition-opacity shadow-md shadow-violet-500/20"
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
          {/* Avatar — small gradient circle for both sides. Assistant gets
              the sparkle glyph; user side is intentionally a glyph-less
              dot so the bubble itself stays the focal point, matching the
              reference design. */}
          <div className={cn(
            "rounded-full flex items-center justify-center flex-shrink-0 brand-gradient text-white shadow-md shadow-violet-500/25 ring-2 ring-white",
            msg.role === 'user' ? "w-5 h-5 mb-1" : "w-8 h-8 mb-1"
          )}>
            {msg.role === 'assistant' && <Sparkles size={14} strokeWidth={2.5} />}
          </div>

          <div className={cn(
            "flex flex-col gap-2 min-w-0",
            msg.role === 'user' ? "items-end" : "items-start"
          )}>
            <div className={cn(
              "px-5 py-3 text-[15px] leading-relaxed max-w-full",
              msg.role === 'user'
                ? "brand-gradient text-white rounded-full rounded-br-md shadow-lg shadow-violet-500/25"
                : "bg-surface border border-border text-gray-800 rounded-2xl rounded-bl-md shadow-[0_4px_24px_-12px_rgba(99,102,241,0.25)]"
            )}>
              <div className={cn(
                "prose max-w-none prose-p:leading-relaxed prose-p:my-1.5",
                msg.role === 'user'
                  ? "prose-invert prose-strong:text-white prose-a:text-white prose-a:underline"
                  : "prose-gray prose-pre:bg-gray-50 prose-pre:border prose-pre:border-border prose-code:text-violet-600 prose-strong:text-gray-900 prose-a:text-violet-600"
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
                  <span key={idx} className="text-[10px] font-bold uppercase tracking-wider px-2 py-1 bg-white border border-border rounded-md text-gray-500">
                    {m}
                  </span>
                ))}
              </div>
            )}

            {msg.trace && msg.trace.length > 0 && (
              <details className="mt-2 w-full">
                <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-700 transition-colors font-medium">
                  View trace details
                </summary>
                <div className="mt-2 space-y-2 p-3 bg-white/70 rounded-xl border border-border">
                  {msg.trace.map((t, idx) => (
                    <div key={idx} className="text-[11px] grid grid-cols-[80px_1fr] gap-2">
                      <span className="text-gray-700 font-semibold">{t.provider || t.stage}:</span>
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
          <div className="w-8 h-8 rounded-full brand-gradient text-white flex items-center justify-center flex-shrink-0 shadow-md shadow-violet-500/25 ring-2 ring-white">
            <Sparkles size={14} strokeWidth={2.5} />
          </div>
          <div className="flex items-center gap-2 px-1 py-2">
            <Loader2 size={16} className="animate-spin text-violet-500" />
            <span className="text-sm text-gray-500 font-medium">Analyzing, please wait…</span>
          </div>
        </div>
      )}
    </div>
  );
};
