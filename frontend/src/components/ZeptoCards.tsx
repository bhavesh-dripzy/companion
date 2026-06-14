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
import { Plus, Minus, Trash2, ShoppingCart, Sparkles, Receipt, Tag } from 'lucide-react';
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
        'flex flex-col w-[130px] shrink-0 snap-start bg-white rounded-xl border border-slate-200/80 overflow-hidden transition-all duration-200 hover:shadow-md hover:-translate-y-0.5',
        outOfStock && 'opacity-60',
      )}
    >
      {/* Image + ADD button overlay */}
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
            <ShoppingCart size={22} style={{ color: theme.brandHex }} />
          </div>
        )}
        {item.is_ad && (
          <span className="absolute top-1.5 left-1.5 text-[8px] font-bold uppercase tracking-wider px-1 py-px rounded bg-slate-900/70 text-white">
            Ad
          </span>
        )}
        {/* ADD button — bottom right corner overlapping image */}
        <button
          type="button"
          onClick={handleAdd}
          disabled={disabled || !onQuickPrompt || outOfStock}
          className={cn(
            'absolute bottom-1.5 right-1.5 w-8 h-8 rounded-lg flex items-center justify-center text-white shadow-md transition-all',
            'hover:brightness-110 active:scale-90',
            'disabled:opacity-50 disabled:cursor-not-allowed',
          )}
          style={{ backgroundColor: theme.brandHex }}
          title={outOfStock ? 'Out of stock' : `Add ${item.name}`}
        >
          <Plus size={16} strokeWidth={2.5} />
        </button>
      </div>

      {/* Info */}
      <div className="flex flex-col px-2 pt-1.5 pb-2 gap-1">
        <p className="text-[11px] font-semibold text-slate-800 leading-snug line-clamp-2 min-h-[26px]">
          {item.name}
        </p>
        {item.pack_size && (
          <p className="text-[9.5px] text-slate-400 truncate">{item.pack_size}</p>
        )}
        <div className="flex items-baseline gap-1.5 mt-0.5">
          {price && (
            <span className="text-[12.5px] font-extrabold text-slate-900">{price}</span>
          )}
          {showStrike && (
            <span className="text-[10px] text-slate-400 line-through">{mrp}</span>
          )}
        </div>
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
    const ids =
      item.product_variant_id && item.store_product_id
        ? ` [pvid:${item.product_variant_id}, spid:${item.store_product_id}]`
        : '';
    if (newQty === 0) {
      onQuickPrompt(`Remove ${name}${ids} from my Zepto cart.`);
    } else {
      onQuickPrompt(`Set quantity of ${name}${ids} to ${newQty} in my Zepto cart.`);
    }
  };

  return (
    <div className="flex items-start gap-3 py-4 px-4 border-b border-slate-100 last:border-b-0">
      {/* Image */}
      <div className="w-[72px] h-[72px] rounded-xl overflow-hidden bg-slate-100 shrink-0 border border-slate-200">
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
            <ShoppingCart size={22} style={{ color: `${theme.brandHex}80` }} />
          </div>
        )}
      </div>

      {/* Name + pack */}
      <div className="flex-1 min-w-0 pt-0.5">
        <p className="text-[13.5px] font-bold text-slate-900 leading-snug line-clamp-2">
          {item.name || 'Item'}
        </p>
        {item.pack_size && (
          <p className="text-[11.5px] text-slate-400 mt-0.5">{item.pack_size}</p>
        )}
      </div>

      {/* Qty pill + price */}
      <div className="flex flex-col items-end gap-2 shrink-0 pt-0.5">
        {/* Quantity pill */}
        <div
          className="flex items-center rounded-xl border overflow-hidden"
          style={{ borderColor: `${theme.brandHex}30` }}
        >
          <button
            type="button"
            onClick={() => setQty(qty - 1)}
            disabled={disabled || !onQuickPrompt}
            className="w-8 h-8 flex items-center justify-center transition-colors hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ color: theme.brandHex }}
            title={qty <= 1 ? `Remove ${item.name}` : 'Decrease'}
          >
            {qty <= 1 ? <Trash2 size={13} /> : <Minus size={13} strokeWidth={2.5} />}
          </button>
          <span
            className="w-7 text-center text-[13px] font-bold"
            style={{ color: theme.brandHex }}
          >
            {qty}
          </span>
          <button
            type="button"
            onClick={() => setQty(qty + 1)}
            disabled={disabled || !onQuickPrompt}
            className="w-8 h-8 flex items-center justify-center transition-colors hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ color: theme.brandHex }}
            title="Increase"
          >
            <Plus size={13} strokeWidth={2.5} />
          </button>
        </div>

        {/* Price */}
        <div className="flex items-baseline gap-1.5">
          {item.mrp_inr && item.mrp_inr > (item.price_inr ?? 0) && (
            <span className="text-[12px] text-slate-400 line-through">
              {formatPriceFromInr(item.mrp_inr)}
            </span>
          )}
          {lineTotalLabel && (
            <span className="text-[15px] font-bold text-green-700">{lineTotalLabel}</span>
          )}
        </div>
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

  const handleClearCart = () => {
    if (!onQuickPrompt || isEmpty) return;
    // Embed every pvid/spid from the cart so the planner can clear even
    // if the session state is cold (no prior view_cart in context).
    const itemLines = items
      .filter((it) => it.product_variant_id && it.store_product_id)
      .map((it) => `pvid=${it.product_variant_id}, spid=${it.store_product_id} (${it.name || 'item'})`)
      .join('\n');
    onQuickPrompt(
      `Clear my entire Zepto cart. Set ALL of these items to quantity 0:\n${itemLines}`,
    );
  };

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
              {!isEmpty && onQuickPrompt && (
                <button
                  type="button"
                  onClick={handleClearCart}
                  disabled={disabled}
                  className={cn(
                    'w-full inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[11.5px] font-semibold text-slate-500 border border-slate-200 bg-white transition-all',
                    'hover:bg-red-50 hover:border-red-200 hover:text-red-600',
                    'disabled:cursor-not-allowed disabled:opacity-50',
                  )}
                  title="Remove all items from cart"
                >
                  <Trash2 size={12} />
                  <span>Clear cart</span>
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
};
