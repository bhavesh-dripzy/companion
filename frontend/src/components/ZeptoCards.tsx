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

import React, { useState, useRef } from 'react';
import { Plus, Minus, Trash2, ShoppingCart, Sparkles, Receipt, Tag, MapPin, ChevronDown } from 'lucide-react';
import type {
  Attachment,
  ZeptoCart,
  ZeptoCartItem,
  ZeptoProductCardEntry,
} from '../types';
import { cn } from '../utils';
import { themeFor } from '../ChatLog';
import { useZeptoAddresses } from '../ZeptoAddressContext';

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

  // Deterministic tap: add THIS exact product by its variant id (+ store_product_id) — no LLM,
  // no re-search, exact variant, works even if the session catalog was wiped. Same pattern as Instamart.
  const handleAdd = () => {
    if (!onQuickPrompt || disabled || outOfStock) return;
    onQuickPrompt(
      `@@A@@${JSON.stringify({
        type: 'add',
        payload: {
          provider: 'zepto',
          item_id: item.product_variant_id,
          secondary_id: item.store_product_id || '',
          name: item.name,
          qty: 1,
        },
      })}`,
    );
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
type ZeptoDeliveryAddr = { id: string; tag: string; line: string };

// Strip a leading recipient prefix the backend may keep on a line.
const cleanZeptoLine = (s: string) => s.replace(/^[^:]+:\s*/, '').trim();

// Full-width delivery-ADDRESS bar shown BELOW the Zepto product cards (mirrors
// Instamart). Zepto delivers to a saved address (select_saved_address binds the
// store + doorstep), so we surface that address with a Change dropdown listing
// every saved address; selecting one fires a switch prompt.
const ZeptoDeliveryAddressBar: React.FC<{
  addressId: string | null | undefined;
  addresses: ZeptoDeliveryAddr[] | null | undefined;
  query?: string;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
  /** Override the natural-language turn a switch fires (defaults to a plain
      address switch). The cart passes a cart-refresh prompt so changing the
      address keeps the user on the cart. */
  promptForAddress?: (addr: ZeptoDeliveryAddr) => string;
}> = ({ addressId, addresses, onQuickPrompt, disabled, promptForAddress }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const theme = themeFor('zepto');
  const ctx = useZeptoAddresses();

  // The card attachment usually carries only the selected id (no list), so fall
  // back to the saved-address list fetched at the App level and the App's
  // selected id when the attachment omits them.
  const effectiveId = addressId || ctx.selectedId || null;
  const effectiveAddresses: ZeptoDeliveryAddr[] =
    addresses && addresses.length
      ? addresses
      : ctx.addresses.map((a) => {
          const [tag, ...rest] = (a.location_name || '').split(' — ');
          return {
            id: a.address_id,
            tag: rest.length ? tag.trim() : '',
            line: rest.length ? rest.join(' — ').trim() : (a.location_name || '').trim(),
          };
        });

  if (!effectiveId && !effectiveAddresses.length) return null;

  const current = effectiveAddresses.find((a) => a.id === effectiveId) || effectiveAddresses[0];
  const displayTag = current?.tag || cleanZeptoLine(current?.line || '').split(',')[0] || 'Delivery address';
  const displayLine = current ? cleanZeptoLine(current.line) : '';
  const canChange = effectiveAddresses.length > 1 && !!onQuickPrompt && !disabled;

  const handleSelect = (addr: ZeptoDeliveryAddr) => {
    setOpen(false);
    if (!onQuickPrompt || disabled || addr.id === (current?.id ?? effectiveId)) return;
    onQuickPrompt(
      promptForAddress
        ? promptForAddress(addr)
        : `Switch my Zepto delivery address to: ${addr.tag || addr.line.split(',')[0]}.`,
    );
  };

  return (
    <div className="relative mt-2 px-1" ref={ref}>
      <div className="flex items-center gap-2.5 rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-sm">
        <span
          className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center"
          style={{ backgroundColor: `${theme.brandHex}1a` }}
        >
          <MapPin size={14} style={{ color: theme.brandHex }} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-medium uppercase tracking-wider text-slate-400 leading-none">
            Delivering to
          </p>
          <p className="text-[12.5px] font-bold text-slate-800 leading-snug truncate mt-0.5">
            {displayTag}
            {displayLine && displayLine !== displayTag && (
              <span className="font-normal text-slate-400"> · {displayLine}</span>
            )}
          </p>
        </div>
        {canChange && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[11px] font-semibold text-slate-600 hover:bg-slate-50 hover:border-slate-300 transition-all active:scale-95"
          >
            Change
            <ChevronDown size={12} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
          </button>
        )}
      </div>

      {open && canChange && (
        <div
          className="absolute right-1 bottom-full mb-1 z-30 bg-white rounded-xl border border-slate-200 shadow-lg min-w-[260px] max-w-[340px] overflow-hidden"
          onMouseLeave={() => setOpen(false)}
        >
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider px-3 pt-2.5 pb-1">
            Deliver to
          </p>
          <div className="max-h-[260px] overflow-y-auto">
            {effectiveAddresses.map((addr) => {
              const isSelected = addr.id === (current?.id ?? effectiveId);
              return (
                <button
                  key={addr.id}
                  type="button"
                  onClick={() => handleSelect(addr)}
                  className={`w-full text-left px-3 py-2 flex items-start gap-2 hover:bg-slate-50 transition-colors ${isSelected ? 'bg-violet-50' : ''}`}
                >
                  <MapPin size={12} className="shrink-0 mt-0.5" style={{ color: isSelected ? theme.brandHex : '#94a3b8' }} />
                  <div className="min-w-0">
                    <p className={`text-[12px] font-semibold leading-snug truncate ${isSelected ? 'text-[#7B2CFA]' : 'text-slate-800'}`}>
                      {addr.tag || cleanZeptoLine(addr.line).split(',')[0]}
                    </p>
                    {addr.line && (
                      <p className="text-[10px] text-slate-400 leading-snug truncate mt-px">
                        {cleanZeptoLine(addr.line)}
                      </p>
                    )}
                  </div>
                  {isSelected && (
                    <span className="ml-auto shrink-0 w-3.5 h-3.5 rounded-full flex items-center justify-center mt-0.5" style={{ backgroundColor: theme.brandHex }}>
                      <svg width="7" height="5" viewBox="0 0 7 5" fill="none"><path d="M1 2.5L2.8 4L6 1" stroke="white" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg>
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

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

  const deliveryAddressId = attachment.delivery_address_id;
  const deliveryAddresses = attachment.delivery_addresses as ZeptoDeliveryAddr[] | null | undefined;
  const primaryQuery = Array.from(groups.keys())[0] || '';

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
      <ZeptoDeliveryAddressBar
        addressId={deliveryAddressId}
        addresses={deliveryAddresses}
        query={primaryQuery}
        onQuickPrompt={onQuickPrompt}
        disabled={disabled}
      />
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
  index?: number;
  /** Cart-wide flag: when ANY item lacks an image we drop thumbnails for every row so the cart
   *  stays visually uniform (no mismatched thumbnail-vs-badge rows). */
  hideImage?: boolean;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ item, index, hideImage, onQuickPrompt, disabled }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const theme = themeFor('zepto');
  const qty = typeof item.quantity === 'number' && item.quantity > 0 ? item.quantity : 1;
  const lineTotal = typeof item.price_inr === 'number' ? item.price_inr * qty : null;
  const lineTotalLabel = formatPriceFromInr(lineTotal ?? undefined);
  const showImage = !hideImage && !!item.image_url && !imageBroken;

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
    <div
      className={cn(
        'flex gap-3 px-4 border-b border-slate-100 last:border-b-0',
        showImage ? 'items-start py-4' : 'items-center py-2.5',
      )}
    >
      {/* Image when we have one; otherwise a compact numbered badge (no placeholder box). */}
      {showImage ? (
        <div className="w-[72px] h-[72px] rounded-xl overflow-hidden bg-slate-100 shrink-0 border border-slate-200">
          <img
            src={item.image_url ?? ''}
            alt={item.name ?? ''}
            loading="lazy"
            onError={() => setImageBroken(true)}
            referrerPolicy="no-referrer"
            className="w-full h-full object-cover"
          />
        </div>
      ) : index ? (
        <span
          className="shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold"
          style={{ backgroundColor: `${theme.brandHex}1a`, color: theme.brandHex }}
        >
          {index}
        </span>
      ) : null}

      {/* Name + pack */}
      <div className="flex-1 min-w-0 pt-0.5">
        <p
          className={cn(
            'text-[13.5px] font-bold text-slate-900 leading-snug',
            showImage ? 'line-clamp-2' : 'line-clamp-1',
          )}
        >
          {item.name || 'Item'}
        </p>
        {item.pack_size && (
          <p className="text-[11.5px] text-slate-400 mt-0.5">{item.pack_size}</p>
        )}
      </div>

      {/* Qty pill + price — one row when compact (no image), stacked when there's a thumbnail */}
      <div
        className={cn(
          'shrink-0',
          showImage ? 'flex flex-col items-end gap-2 pt-0.5' : 'flex items-center gap-2.5',
        )}
      >
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
        <div className="flex items-baseline justify-end gap-1.5 min-w-[46px]">
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
  addressId?: string | null;
  addresses?: ZeptoDeliveryAddr[] | null;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ cart, addressId, addresses, onQuickPrompt, disabled }) => {
  if (!cart) return null;
  const theme = themeFor('zepto');
  const items = Array.isArray(cart.items) ? cart.items : [];
  const isEmpty = !!cart.is_empty || items.length === 0;
  const totalLabel = formatPriceFromPaise(cart.grand_total_paise);
  // All-or-nothing thumbnails: one image-less item → drop images for every row (uniform layout).
  const hideImages = items.some((it) => !it.image_url);

  const handleCheckout = onQuickPrompt && !isEmpty
    ? () => onQuickPrompt(theme.checkoutPrompt)
    : undefined;

  const handleClearCart = () => {
    if (!onQuickPrompt || isEmpty) return;
    // Deterministic clear: a `clear` client_action goes straight to the provider's native
    // clear (Zepto: update_cart replaceCart=[]) — no LLM/resolver, no per-item qty-0 dance.
    onQuickPrompt(`@@A@@${JSON.stringify({ type: 'clear', payload: { provider: 'zepto' } })}`);
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
                  index={idx + 1}
                  hideImage={hideImages}
                  onQuickPrompt={onQuickPrompt}
                  disabled={disabled}
                />
              ))}
            </div>
            <div className="px-4 py-3 bg-slate-50/60 border-t border-slate-100 space-y-2">
              {cart.unavailable && (
                <div className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 flex items-center gap-2">
                  <span className="flex-1 text-[11.5px] text-amber-700">⚠️ {cart.unavailable}</span>
                  {onQuickPrompt && (
                    <button
                      type="button"
                      onClick={() => onQuickPrompt('Zepto ka delivery address change karna hai')}
                      disabled={disabled}
                      className="shrink-0 rounded-lg border border-amber-300 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-amber-700 hover:bg-amber-100 transition-all active:scale-95 disabled:opacity-50"
                    >
                      Change Address
                    </button>
                  )}
                </div>
              )}
              {Array.isArray(cart.bill_breakdown) && cart.bill_breakdown.length > 0 && (
                <div className="space-y-1 pb-1.5 border-b border-slate-200/70">
                  {cart.bill_breakdown.map((line, i) => (
                    <div
                      key={`${line.label}-${i}`}
                      className="flex items-center justify-between text-[11.5px] text-slate-500"
                    >
                      <span>{line.label}</span>
                      <span className="font-medium text-slate-600">{line.value}</span>
                    </div>
                  ))}
                </div>
              )}
              {/* Bold total = the FINAL Zepto price (get_payment_methods). Only shown when we
                  actually have it (store open); when closed, the "Item Total" bill line is enough,
                  so we don't repeat our own item-sum as if it were the payable amount. */}
              {!cart.unavailable && (
                <div className="flex items-center justify-between text-[12px] font-semibold text-slate-700">
                  <span className="inline-flex items-center gap-1.5">
                    <Receipt size={12} style={{ color: theme.brandHex }} />
                    Total
                  </span>
                  <span className="text-[13.5px] font-extrabold text-slate-900">
                    {totalLabel || 'calculated at checkout'}
                  </span>
                </div>
              )}
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
      {/* Always show WHERE this cart ships — and let the user switch it right from
          the cart. The cart-refresh prompt keeps them on the cart at the new
          address. Falls back to the App-level saved-address context for the list. */}
      <ZeptoDeliveryAddressBar
        addressId={addressId}
        addresses={addresses}
        onQuickPrompt={onQuickPrompt}
        disabled={disabled}
        promptForAddress={(addr) =>
          `Switch my Zepto delivery address to ${addr.tag || addr.line.split(',')[0]} and show my updated cart.`
        }
      />
    </div>
  );
};
