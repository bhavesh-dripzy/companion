// Zepto-specific chat card components.
//
// Two card kinds:
//   * `zepto_product_cards` — grid of grocery products with a one-tap
//     "Add to cart" action. The button synthesises a `update_cart` intent
//     ("Add <product name> to my Zepto cart") and pushes it through the
//     same /api/agent/query/ pipeline as a typed message, so the planner
//     copies the pvid/spid out of session_zepto_products and the executor
//     adds it without a round-trip search.
//   * `zepto_cart_card` — live receipt of the current Zepto cart, with a
//     "Place order" CTA that fires the provider-specific checkout prompt.
//
// Both cards use the `themeFor('zepto')` palette (violet `#7B2CFA`) so
// Zepto turns are visually distinct from Zomato red / Swiggy orange /
// District purple in the chat scroll.

import React, { useState } from 'react';
import { Plus, ShoppingCart, Sparkles, Receipt, Tag } from 'lucide-react';
import type {
  Attachment,
  ZeptoCart,
  ZeptoCartItem,
  ZeptoProductCardEntry,
} from '../types';
import { cn } from '../utils';
import { themeFor } from '../ChatLog';

const formatPriceFromInr = (value: number | null | undefined): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
  // Zepto prices are rupees with up to 2 decimals (₹54.5, ₹46); round
  // up to the nearest rupee for the card label so prices stay scannable.
  return `₹${value % 1 === 0 ? value.toFixed(0) : value.toFixed(2)}`;
};

const formatPriceFromPaise = (paise: number | null | undefined): string | null => {
  if (typeof paise !== 'number' || !Number.isFinite(paise) || paise < 0) return null;
  return formatPriceFromInr(paise / 100);
};

// Shared header for every Zepto card grid — brand pill + label + count.
const ZeptoGridHeader: React.FC<{
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
}> = ({ icon, title, subtitle }) => {
  const theme = themeFor('zepto');
  return (
    <div className="flex items-center gap-2 px-1 mb-2">
      <div
        className="flex items-center gap-1.5 px-2 py-0.5 rounded-md text-white text-[10px] font-bold tracking-wider uppercase"
        style={{ backgroundColor: theme.brandHex }}
      >
        {icon}
        <span>{theme.label}</span>
      </div>
      <span className="text-xs font-bold uppercase tracking-wider text-slate-500 truncate">{title}</span>
      {subtitle && (
        <span className="text-[10px] text-slate-400 ml-1 truncate">{subtitle}</span>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Single product tile. Two-column layout: square image on the left, name +
// price + pack + Add-to-cart button on the right.
//
// The "Add" CTA fires an LLM-friendly prompt that names the exact product
// so the planner uses the session-cached pvid/spid (it doesn't have to
// re-search Zepto, which would return a different pvid for the same
// item and silently break the cart flow).
// ---------------------------------------------------------------------------
const ZeptoProductTile: React.FC<{
  item: ZeptoProductCardEntry;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ item, onQuickPrompt, disabled }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const theme = themeFor('zepto');
  const price = formatPriceFromInr(item.price_inr) ?? item.price_text ?? null;
  const mrp = formatPriceFromInr(item.mrp_inr);
  const showStrike = !!mrp && !!price && item.mrp_inr! > (item.price_inr ?? 0);
  const showImage = !!item.image_url && !imageBroken;
  const outOfStock =
    typeof item.available_quantity === 'number' && item.available_quantity <= 0;

  const handleAdd = () => {
    if (!onQuickPrompt || disabled || outOfStock) return;
    // Keep the brand word ("Zepto") in the prompt so the orchestrator's
    // provider stickiness pins this follow-up to the zepto MCP even if
    // the agent had drifted between turns.
    onQuickPrompt(`Add 1 ${item.name} to my Zepto cart.`);
  };

  return (
    <div
      className={cn(
        'flex flex-col w-[170px] shrink-0 snap-start bg-white rounded-2xl border border-slate-200/80 overflow-hidden transition-all duration-200 hover:shadow-md hover:-translate-y-0.5',
        outOfStock && 'opacity-60',
      )}
    >
      <div className="relative aspect-square bg-slate-50 overflow-hidden">
        {showImage ? (
          <img
            src={item.image_url ?? ''}
            alt={item.name}
            loading="lazy"
            onError={() => setImageBroken(true)}
            referrerPolicy="no-referrer"
            className="w-full h-full object-cover"
          />
        ) : (
          <div
            className="w-full h-full flex items-center justify-center"
            style={{ background: `linear-gradient(135deg, ${theme.brandHex}1a, transparent)` }}
          >
            <ShoppingCart size={26} style={{ color: theme.brandHex }} />
          </div>
        )}
        {item.is_ad && (
          <span className="absolute top-1.5 left-1.5 text-[8px] font-bold uppercase tracking-wider px-1 py-px rounded bg-slate-900/70 text-white">
            Ad
          </span>
        )}
      </div>

      <div className="flex flex-col flex-1 px-2.5 pt-2 pb-2.5 gap-1">
        <p className="text-[11.5px] font-semibold text-slate-800 leading-snug line-clamp-2 min-h-[28px]">
          {item.name}
        </p>
        {item.pack_size && (
          <p className="text-[10px] text-slate-400 truncate">{item.pack_size}</p>
        )}
        <div className="flex items-baseline gap-1.5 mt-auto">
          {price && (
            <span className="text-[12.5px] font-extrabold text-slate-900">{price}</span>
          )}
          {showStrike && (
            <span className="text-[10px] text-slate-400 line-through">{mrp}</span>
          )}
        </div>
        <button
          type="button"
          onClick={handleAdd}
          disabled={disabled || !onQuickPrompt || outOfStock}
          className={cn(
            'mt-1 inline-flex items-center justify-center gap-1 rounded-lg px-2 py-1.5 text-[11px] font-bold uppercase tracking-wider text-white transition-all',
            'hover:brightness-110 active:scale-95',
            'disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:brightness-100 disabled:active:scale-100',
          )}
          style={{ backgroundColor: theme.brandHex }}
          title={
            outOfStock
              ? 'Out of stock'
              : disabled
                ? 'Waiting for current reply'
                : `Add ${item.name} to your Zepto cart`
          }
        >
          {outOfStock ? (
            <span>Out of stock</span>
          ) : (
            <>
              <Plus size={11} strokeWidth={3} />
              <span>Add</span>
            </>
          )}
        </button>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Product grid — horizontal scroller, mirrors the menu/restaurant grids.
// When the backend used `search_multiple_products`, every product carries
// a `query` field; we group them so each query gets its own labeled row.
// ---------------------------------------------------------------------------
export const ZeptoProductCardGrid: React.FC<{
  attachment: Attachment;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ attachment, onQuickPrompt, disabled }) => {
  const products = Array.isArray(attachment.products) ? attachment.products : [];
  if (products.length === 0) return null;

  // Group by query (only meaningful for search_multiple_products); each
  // section becomes its own labelled scroller. Single-query searches just
  // render under a generic "Grocery picks" header.
  const groups = new Map<string, ZeptoProductCardEntry[]>();
  for (const p of products) {
    const key = (p.query || '').trim() || '';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(p);
  }

  return (
    <div className="mt-2 w-full max-w-full space-y-3">
      {Array.from(groups.entries()).map(([query, items], gIdx) => (
        <div key={query || `group-${gIdx}`} className="w-full">
          <ZeptoGridHeader
            icon={<Sparkles size={11} />}
            title={query ? `Results for "${query}"` : 'Grocery picks'}
            subtitle={`${items.length} item${items.length === 1 ? '' : 's'}`}
          />
          <div className="flex flex-row gap-2.5 overflow-x-auto snap-x snap-mandatory pb-2 -mx-1 px-1 [scrollbar-width:thin] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-thumb]:bg-slate-300 [&::-webkit-scrollbar-thumb]:rounded-full">
            {items.map((p, idx) => (
              <ZeptoProductTile
                key={p.product_variant_id || `${p.name}-${idx}`}
                item={p}
                onQuickPrompt={onQuickPrompt}
                disabled={disabled}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Cart row — one line per item with thumbnail + name + qty + price.
// Quantity controls fire follow-up prompts so the planner can build a
// fresh `update_cart` with the new desired quantity (Zepto's update_cart
// is "set quantity to N", not "add 1 more", so we re-emit the absolute
// number).
// ---------------------------------------------------------------------------
const ZeptoCartItemRow: React.FC<{
  item: ZeptoCartItem;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ item, onQuickPrompt, disabled }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const theme = themeFor('zepto');
  const qty = typeof item.quantity === 'number' && item.quantity > 0 ? item.quantity : 1;
  const price = formatPriceFromInr(item.price_inr) ?? item.price_text ?? null;
  const lineTotal = typeof item.price_inr === 'number' ? item.price_inr * qty : null;
  const lineTotalLabel = formatPriceFromInr(lineTotal ?? undefined);
  const showImage = !!item.image_url && !imageBroken;

  const setQty = (newQty: number) => {
    if (!onQuickPrompt || disabled) return;
    const name = item.name || 'this item';
    if (newQty === 0) {
      onQuickPrompt(`Remove ${name} from my Zepto cart.`);
    } else {
      onQuickPrompt(`Set the quantity of ${name} in my Zepto cart to ${newQty}.`);
    }
  };

  return (
    <div className="flex items-center gap-3 py-2.5 px-4 border-b border-slate-100 last:border-b-0">
      <div className="w-11 h-11 rounded-lg overflow-hidden bg-slate-100 shrink-0 border border-slate-200/70">
        {showImage ? (
          <img
            src={item.image_url ?? ''}
            alt={item.name ?? ''}
            loading="lazy"
            onError={() => setImageBroken(true)}
            referrerPolicy="no-referrer"
            className="w-full h-full object-cover"
          />
        ) : (
          <div
            className="w-full h-full flex items-center justify-center"
            style={{ background: `linear-gradient(135deg, ${theme.brandHex}14, transparent)` }}
          >
            <ShoppingCart size={16} style={{ color: theme.brandHex }} />
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[12.5px] font-semibold text-slate-800 leading-tight truncate">
          {item.name || 'Item'}
        </p>
        {item.pack_size && (
          <p className="text-[10px] text-slate-400 truncate">{item.pack_size}</p>
        )}
        {price && (
          <p className="text-[11px] text-slate-500 mt-0.5">
            {price}
            <span className="text-slate-400"> × {qty}</span>
            {lineTotalLabel && (
              <span className="text-slate-800 font-semibold"> = {lineTotalLabel}</span>
            )}
          </p>
        )}
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <button
          type="button"
          onClick={() => setQty(qty - 1)}
          disabled={disabled || !onQuickPrompt}
          className={cn(
            'w-7 h-7 rounded-md text-[14px] font-bold flex items-center justify-center text-slate-600 bg-slate-100 hover:bg-slate-200 transition-colors',
            'disabled:opacity-40 disabled:cursor-not-allowed',
          )}
          title={qty <= 1 ? `Remove ${item.name}` : `Decrease quantity`}
        >
          −
        </button>
        <span className="w-7 text-center text-[12px] font-bold text-slate-800">{qty}</span>
        <button
          type="button"
          onClick={() => setQty(qty + 1)}
          disabled={disabled || !onQuickPrompt}
          className={cn(
            'w-7 h-7 rounded-md text-[14px] font-bold flex items-center justify-center text-white transition-colors',
            'disabled:opacity-40 disabled:cursor-not-allowed',
          )}
          style={{ backgroundColor: theme.brandHex }}
          title={`Increase quantity of ${item.name}`}
        >
          +
        </button>
      </div>
    </div>
  );
};

// Full cart card. Mirrors the Zomato CartCard's column split but is
// considerably simpler since Zepto doesn't surface a tax/fees breakdown
// in its `view_cart` response — only the running total.
export const ZeptoCartCard: React.FC<{
  cart: ZeptoCart;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ cart, onQuickPrompt, disabled }) => {
  if (!cart) return null;
  const theme = themeFor('zepto');
  const items = Array.isArray(cart.items) ? cart.items : [];
  const isEmpty = !!cart.is_empty || items.length === 0;
  const totalLabel = formatPriceFromPaise(cart.grand_total_paise);

  const handleCheckout = onQuickPrompt && !isEmpty
    ? () => onQuickPrompt(theme.checkoutPrompt)
    : undefined;

  return (
    <div className="mt-3 w-full max-w-full md:max-w-md animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div
          className="flex items-center justify-between px-4 py-2.5"
          style={{
            background: `linear-gradient(to right, ${theme.brandHex}26, transparent)`,
            borderBottomColor: `${theme.brandHex}33`,
            borderBottomWidth: 1,
          }}
        >
          <div className="flex items-center gap-1.5">
            <ShoppingCart size={14} style={{ color: theme.brandHex }} />
            <span
              className="text-[11px] font-bold uppercase tracking-wider"
              style={{ color: theme.brandHex }}
            >
              {theme.cartHeaderTitle}
            </span>
          </div>
          {!isEmpty && (
            <span className="text-[10px] text-slate-500 font-semibold">
              {items.length} item{items.length === 1 ? '' : 's'}
            </span>
          )}
        </div>

        {isEmpty ? (
          <div className="px-4 py-6 text-center">
            <ShoppingCart size={28} className="mx-auto text-slate-300 mb-2" />
            <p className="text-[13px] font-semibold text-slate-600">Your Zepto cart is empty.</p>
            <p className="text-[11px] text-slate-400 mt-1">
              {cart.summary_text || 'Add some groceries to get started.'}
            </p>
          </div>
        ) : (
          <>
            <div>
              {items.map((it, idx) => (
                <ZeptoCartItemRow
                  key={it.product_variant_id || `${it.name}-${idx}`}
                  item={it}
                  onQuickPrompt={onQuickPrompt}
                  disabled={disabled}
                />
              ))}
            </div>
            <div className="px-4 py-3 bg-slate-50/60 border-t border-slate-100 space-y-2">
              <div className="flex items-center justify-between text-[12px] font-semibold text-slate-700">
                <span className="inline-flex items-center gap-1.5">
                  <Receipt size={12} style={{ color: theme.brandHex }} />
                  Total
                </span>
                <span className="text-[13.5px] font-extrabold text-slate-900">
                  {totalLabel || 'calculated at checkout'}
                </span>
              </div>
              {handleCheckout && (
                <button
                  type="button"
                  onClick={handleCheckout}
                  disabled={disabled}
                  className={cn(
                    'w-full inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-[12.5px] font-bold uppercase tracking-wider text-white transition-all',
                    'hover:brightness-110 active:scale-[0.99]',
                    'disabled:cursor-not-allowed disabled:opacity-50',
                  )}
                  style={{ backgroundColor: theme.brandHex }}
                  title={disabled ? 'Waiting for current reply' : 'Place this Zepto order'}
                >
                  <Tag size={12} />
                  <span>Place order</span>
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
};
