// Renders the new agent backend's typed cards (product_list / cart) + a pending
// disambiguation, inside the existing chat bubble. Taps emit a deterministic
// client_action encoded as a "@@A@@<json>" prompt through the existing
// onQuickPrompt plumbing (so no new prop threading) — handleSendMessage decodes it.

import { useState } from 'react';
import { Plus, ShoppingCart } from 'lucide-react';
import type { AgentCard, AgentPendingAction } from '../types';

const PROVIDER_LABEL: Record<string, string> = {
  instamart: 'Instamart', zepto: 'Zepto', swiggy_food: 'Swiggy', zomato: 'Zomato',
};

// Per-provider accent so the disambiguation cards read as the same family as the search cards.
const ACCENT: Record<string, string> = {
  instamart: '#f9621e', zepto: '#7b2ff7', swiggy_food: '#fc8019', zomato: '#e23744',
};

const act = (type: string, payload: any): string => `@@A@@${JSON.stringify({ type, payload })}`;

function ProviderTag({ provider }: { provider?: string }) {
  if (!provider) return null;
  return (
    <span className="text-[10px] font-semibold uppercase tracking-wide text-[#2b2f72]/70 bg-[#2b2f72]/8 px-1.5 py-0.5 rounded">
      {PROVIDER_LABEL[provider] || provider}
    </span>
  );
}

function ProductList({ card, onAction, disabled }: {
  card: AgentCard; onAction?: (p: string) => void; disabled?: boolean;
}) {
  return (
    <div className="rounded-xl border border-[#ece3d6] bg-white/90 p-2.5 shadow-2xs">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[12px] font-semibold text-[#221b2e] capitalize">{card.title || 'Results'}</span>
        <ProviderTag provider={card.provider} />
      </div>
      <div className="flex flex-col divide-y divide-[#f0ebe3]">
        {(card.items || []).map((it) => (
          <div key={it.ref} className="flex items-center gap-2 py-1.5">
            <div className="min-w-0 flex-1">
              <div className="text-[13px] text-[#221b2e] truncate">{it.name}</div>
              <div className="text-[12px] text-[#c2630f] font-semibold">{it.price}</div>
            </div>
            <button
              disabled={disabled || it.in_stock === false}
              onClick={() => onAction?.(act('add', { provider: card.provider, ref: it.ref, qty: 1 }))}
              className="shrink-0 text-[12px] font-semibold text-white bg-gradient-to-br from-[#2b2f72] to-[#e85d7a] px-3 py-1 rounded-full disabled:opacity-40"
            >
              {it.in_stock === false ? 'Out' : 'Add'}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function CartCard({ card, onAction, disabled }: {
  card: AgentCard; onAction?: (p: string) => void; disabled?: boolean;
}) {
  if (card.empty) {
    return (
      <div className="rounded-xl border border-[#ece3d6] bg-white/90 p-2.5 text-[12px] text-slate-500">
        <ProviderTag provider={card.provider} /> <span className="ml-1">Cart is empty</span>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-[#ece3d6] bg-white/90 p-2.5 shadow-2xs">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[12px] font-semibold text-[#221b2e]">Cart</span>
        <ProviderTag provider={card.provider} />
      </div>
      <div className="flex flex-col divide-y divide-[#f0ebe3]">
        {(card.items || []).map((it) => (
          <div key={it.item_id || it.name} className="flex items-center gap-2 py-1.5">
            <div className="min-w-0 flex-1 text-[13px] text-[#221b2e] truncate">{it.name}</div>
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                disabled={disabled}
                onClick={() => onAction?.(act('set_qty', { provider: card.provider, item_id: it.item_id, qty: Math.max(0, (it.qty || 1) - 1) }))}
                className="w-6 h-6 rounded-full border border-[#ece3d6] text-[#2b2f72] font-bold disabled:opacity-40"
              >−</button>
              <span className="text-[13px] w-5 text-center">{it.qty}</span>
              <button
                disabled={disabled}
                onClick={() => onAction?.(act('set_qty', { provider: card.provider, item_id: it.item_id, qty: (it.qty || 1) + 1 }))}
                className="w-6 h-6 rounded-full border border-[#ece3d6] text-[#2b2f72] font-bold disabled:opacity-40"
              >+</button>
            </div>
            <div className="text-[12px] text-[#c2630f] font-semibold w-12 text-right">{it.price}</div>
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between mt-2 pt-1.5 border-t border-[#ece3d6]">
        <span className="text-[12px] font-semibold text-[#221b2e]">Total {card.total}</span>
        <button
          disabled={disabled}
          onClick={() => onAction?.(act('checkout', { provider: card.provider }))}
          className="text-[12px] font-semibold text-white bg-[#2b2f72] px-3 py-1 rounded-full disabled:opacity-40"
        >Checkout</button>
      </div>
    </div>
  );
}

// One pickable option, rendered exactly like a search product card (square image / fallback,
// name, size pill, price, corner Add button) so disambiguation feels native, not like a text list.
function ChoiceCard({ choice, onAction, disabled }: {
  choice: any; onAction?: (p: string) => void; disabled?: boolean;
}) {
  const [broken, setBroken] = useState(false);
  const accent = ACCENT[choice.provider] || '#2b2f72';
  const showImage = !!choice.image && !broken;
  return (
    <div className="flex flex-col w-[130px] shrink-0 snap-start bg-white rounded-xl border border-slate-200/80 overflow-hidden transition-all duration-200 hover:shadow-md hover:-translate-y-0.5">
      <div className="relative aspect-square bg-slate-50 overflow-hidden">
        {showImage ? (
          <img
            src={choice.image}
            alt={choice.name}
            loading="lazy"
            onError={() => setBroken(true)}
            referrerPolicy="no-referrer"
            className="w-full h-full object-cover"
          />
        ) : (
          <div
            className="w-full h-full flex items-center justify-center"
            style={{ background: `linear-gradient(135deg, ${accent}1a, transparent)` }}
          >
            <ShoppingCart size={22} style={{ color: accent }} />
          </div>
        )}
        <button
          type="button"
          disabled={disabled}
          onClick={() => onAction?.(act('add', { provider: choice.provider, ref: choice.ref, qty: 1 }))}
          className="absolute bottom-1.5 right-1.5 w-8 h-8 rounded-lg flex items-center justify-center text-white shadow-md transition-all hover:brightness-110 active:scale-90 disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ backgroundColor: accent }}
          title={`Add ${choice.name}`}
        >
          <Plus size={16} strokeWidth={2.5} />
        </button>
      </div>
      <div className="flex flex-col px-2 pt-1.5 pb-2 gap-1">
        <p className="text-[11px] font-semibold text-slate-800 leading-snug line-clamp-2 min-h-[26px]">
          {choice.name}
        </p>
        {choice.size && (
          <span className="self-start px-1.5 py-0.5 rounded-md border border-slate-300 text-[9.5px] font-semibold text-slate-600 bg-white leading-none">
            {choice.size}
          </span>
        )}
        {choice.price && (
          <span className="text-[12.5px] font-extrabold text-slate-900 mt-0.5">{choice.price}</span>
        )}
      </div>
    </div>
  );
}

function Disambiguate({ pending, onAction, disabled }: {
  pending: AgentPendingAction; onAction?: (p: string) => void; disabled?: boolean;
}) {
  const opts: any[] = pending.options || [];
  return (
    <div className="mt-3 w-full max-w-full md:max-w-md flex flex-col gap-3 animate-fade-in">
      {opts.map((o, i) => (
        <div key={i}>
          {/* Per-item prompt so a multi-item ask ("bread" vs "milk") stays unambiguous. */}
          <div className="text-[12px] font-semibold text-slate-700 mb-1.5 px-0.5">
            {o.phrase ? <>&ldquo;{o.phrase}&rdquo; — kaunsa wala?</> : 'Kaunsa wala?'}
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1 snap-x scrollbar-thin">
            {(o.choices || o.refs || []).map((c: any, j: number) => {
              const choice =
                typeof c === 'string'
                  ? { ref: c, name: c, provider: o.provider }
                  : { provider: o.provider, ...c };
              return (
                <ChoiceCard key={choice.ref || j} choice={choice} onAction={onAction} disabled={disabled} />
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

export function AgentCards({ cards, pending, onAction, disabled }: {
  cards?: AgentCard[];
  pending?: AgentPendingAction | null;
  onAction?: (prompt: string) => void;
  disabled?: boolean;
}) {
  if ((!cards || cards.length === 0) && !pending) return null;
  return (
    <div className="mt-2 flex flex-col gap-2 w-full max-w-[420px]">
      {(cards || []).map((c, i) => {
        if (c.type === 'product_list') return <ProductList key={i} card={c} onAction={onAction} disabled={disabled} />;
        if (c.type === 'cart') return <CartCard key={i} card={c} onAction={onAction} disabled={disabled} />;
        if (c.type === 'error') return (
          <div key={i} className="rounded-xl border border-rose-200 bg-rose-50 p-2 text-[12px] text-rose-700">{c.message}</div>
        );
        return null;
      })}
      {pending && pending.type === 'disambiguate' && (
        <Disambiguate pending={pending} onAction={onAction} disabled={disabled} />
      )}
    </div>
  );
}
