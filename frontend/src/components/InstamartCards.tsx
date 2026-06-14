// Swiggy Instamart chat card components.
//
// One card per product (not per variant). The last variant in the array
// (smallest/cheapest pack — Instamart returns descending order) is shown
// by default. Clicking the variant pill opens a bottom sheet where the
// user picks a pack size; the ADD button fires a natural-language prompt
// that the planner resolves to the correct spinId via session context.

import React, { useState } from 'react';
import { Plus, Minus, Trash2, ShoppingCart, X, Receipt, Tag } from 'lucide-react';
import type {
  Attachment,
  InstamartCart,
  InstamartCartItem,
  InstamartProductCardEntry,
  InstamartVariant,
} from '../types';
import { cn } from '../utils';
import { themeFor } from '../ChatLog';

const formatPriceFromInr = (value: number | null | undefined): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
  return `₹${value % 1 === 0 ? value.toFixed(0) : value.toFixed(2)}`;
};

const formatPriceFromPaise = (paise: number | null | undefined): string | null => {
  if (typeof paise !== 'number' || !Number.isFinite(paise) || paise < 0) return null;
  return formatPriceFromInr(paise / 100);
};

const InstamartGridHeader: React.FC<{
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
}> = ({ icon, title, subtitle }) => {
  const theme = themeFor('instamart');
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

// Bottom sheet for picking a variant (pack size).
const VariantSelectorSheet: React.FC<{
  product: InstamartProductCardEntry;
  selectedIdx: number;
  onSelect: (idx: number) => void;
  onAdd: (variant: InstamartVariant) => void;
  onClose: () => void;
  disabled?: boolean;
}> = ({ product, selectedIdx, onSelect, onAdd, onClose, disabled }) => {
  const theme = themeFor('instamart');
  const selected = product.variants[selectedIdx];

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div
        className="relative w-full max-w-lg bg-white rounded-t-2xl shadow-xl pb-safe"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 pt-4 pb-2">
          <div>
            <p className="text-[15px] font-bold text-slate-900">Select variant</p>
            {selected?.quantity_description && (
              <p className="text-[12px] text-slate-400 mt-0.5">
                Quantity: {selected.quantity_description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-full bg-slate-100 text-slate-500 hover:bg-slate-200"
          >
            <X size={14} />
          </button>
        </div>

        {/* Variant grid */}
        <div className="px-4 pb-3 grid grid-cols-3 gap-2.5 max-h-[45vh] overflow-y-auto">
          {product.variants.map((v, idx) => {
            const isSelected = idx === selectedIdx;
            const outOfStock = v.in_stock === false;
            const price = formatPriceFromInr(v.price_inr);
            return (
              <button
                key={v.spin_id}
                type="button"
                disabled={outOfStock}
                onClick={() => onSelect(idx)}
                className={cn(
                  'flex flex-col items-center gap-1.5 p-2 rounded-xl border-2 transition-all text-left',
                  isSelected ? 'border-blue-500 bg-blue-50/50' : 'border-slate-200 bg-white hover:border-slate-300',
                  outOfStock && 'opacity-40 cursor-not-allowed',
                )}
              >
                <div className="w-full aspect-square rounded-lg overflow-hidden bg-slate-50">
                  {v.image_url ? (
                    <img
                      src={v.image_url}
                      alt={v.quantity_description ?? product.name}
                      loading="lazy"
                      referrerPolicy="no-referrer"
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <div
                      className="w-full h-full flex items-center justify-center"
                      style={{ background: `${theme.brandHex}14` }}
                    >
                      <ShoppingCart size={18} style={{ color: theme.brandHex }} />
                    </div>
                  )}
                </div>
                <p className="text-[11px] font-semibold text-slate-700 text-center leading-tight">
                  {v.quantity_description || '—'}
                </p>
                {price && (
                  <p className="text-[12px] font-bold text-slate-900">{price}</p>
                )}
              </button>
            );
          })}
        </div>

        {/* Bottom bar */}
        <div className="border-t border-slate-100 px-4 py-3 flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg overflow-hidden bg-slate-100 shrink-0">
            {selected?.image_url ? (
              <img
                src={selected.image_url}
                alt={product.name}
                referrerPolicy="no-referrer"
                className="w-full h-full object-cover"
              />
            ) : (
              <div className="w-full h-full flex items-center justify-center" style={{ background: `${theme.brandHex}14` }}>
                <ShoppingCart size={14} style={{ color: theme.brandHex }} />
              </div>
            )}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-[12px] font-semibold text-slate-800 truncate">{product.name}</p>
            <p className="text-[10px] text-slate-400 truncate">{product.brand || ''}</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {selected && (
              <span className="text-[13px] font-bold text-slate-900">
                {formatPriceFromInr(selected.price_inr)}
              </span>
            )}
            <button
              type="button"
              disabled={disabled || !selected || selected.in_stock === false}
              onClick={() => selected && onAdd(selected)}
              className={cn(
                'px-5 py-2 rounded-xl text-[13px] font-bold text-white transition-all',
                'hover:brightness-110 active:scale-95',
                'disabled:opacity-50 disabled:cursor-not-allowed',
              )}
              style={{ backgroundColor: theme.brandHex }}
            >
              ADD
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

// Single product card — one card per product, smaller footprint.
const InstamartProductTile: React.FC<{
  item: InstamartProductCardEntry;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ item, onQuickPrompt, disabled }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  // Default to last variant (smallest/cheapest — Instamart returns descending)
  const [selectedIdx, setSelectedIdx] = useState(item.variants.length - 1);
  const theme = themeFor('instamart');

  const variant = item.variants[selectedIdx] ?? item.variants[0];
  const outOfStock = variant?.in_stock === false;
  const price = formatPriceFromInr(variant?.price_inr);
  const showImage = !!variant?.image_url && !imageBroken;
  const hasMoreVariants = item.variants.length > 1;

  const buildPrompt = (v: InstamartVariant) => {
    const brand = (item.brand || '').trim();
    const headline = [brand, item.name].filter(Boolean).join(' ');
    const pack = (v.quantity_description || '').trim();
    return `Add 1 ${headline}${pack ? ` (${pack})` : ''} to my Instamart cart.`;
  };

  const handleAdd = (v: InstamartVariant) => {
    if (!onQuickPrompt || disabled || v.in_stock === false) return;
    setSheetOpen(false);
    onQuickPrompt(buildPrompt(v));
  };

  return (
    <>
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
              src={variant.image_url ?? ''}
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
          {item.is_promoted && (
            <span className="absolute top-1.5 left-1.5 text-[8px] font-bold uppercase tracking-wider px-1 py-px rounded bg-slate-900/70 text-white">
              ★ Ad
            </span>
          )}
          {/* ADD button — bottom right corner overlapping image */}
          <button
            type="button"
            onClick={() => !disabled && !outOfStock && (hasMoreVariants ? setSheetOpen(true) : handleAdd(variant))}
            disabled={disabled || outOfStock}
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
          {/* Variant pill(s) */}
          <div className="flex items-center gap-1 flex-wrap">
            <button
              type="button"
              onClick={() => hasMoreVariants && setSheetOpen(true)}
              className={cn(
                'px-1.5 py-0.5 rounded-md border border-slate-300 text-[9.5px] font-semibold text-slate-600 bg-white leading-none',
                hasMoreVariants && 'hover:border-slate-400 cursor-pointer',
                !hasMoreVariants && 'cursor-default',
              )}
            >
              {variant?.quantity_description || '—'}
            </button>
            {hasMoreVariants && (
              <button
                type="button"
                onClick={() => setSheetOpen(true)}
                className="px-1.5 py-0.5 rounded-md text-[9.5px] font-bold leading-none hover:opacity-80"
                style={{ backgroundColor: `${theme.brandHex}18`, color: theme.brandHex, border: `1px solid ${theme.brandHex}40` }}
              >
                +{item.variants.length - 1}
              </button>
            )}
          </div>
          {/* Price */}
          {price && (
            <div className="flex items-baseline gap-1.5 mt-0.5">
              <span className="text-[12.5px] font-extrabold text-slate-900">{price}</span>
              {variant?.mrp_inr && variant.mrp_inr > (variant.price_inr ?? 0) && (
                <span className="text-[10px] text-slate-400 line-through">
                  {formatPriceFromInr(variant.mrp_inr)}
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {sheetOpen && (
        <VariantSelectorSheet
          product={item}
          selectedIdx={selectedIdx}
          onSelect={setSelectedIdx}
          onAdd={handleAdd}
          onClose={() => setSheetOpen(false)}
          disabled={disabled}
        />
      )}
    </>
  );
};

export const InstamartProductCardGrid: React.FC<{
  attachment: Attachment;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ attachment, onQuickPrompt, disabled }) => {
  const products = Array.isArray(attachment.products) ? attachment.products : [];
  if (products.length === 0) return null;

  const groups = new Map<string, InstamartProductCardEntry[]>();
  for (const p of products) {
    const entry = p as unknown as InstamartProductCardEntry;
    const key = (entry.query || '').trim() || '';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(entry);
  }

  return (
    <div className="mt-2 w-full max-w-full space-y-3">
      {Array.from(groups.entries()).map(([query, items], gIdx) => (
        <div key={query || `group-${gIdx}`} className="w-full">
          <InstamartGridHeader
            icon={<ShoppingCart size={11} />}
            title={query ? `"${query}"` : 'Grocery picks'}
            subtitle={`${items.length} product${items.length === 1 ? '' : 's'}`}
          />
          <div className="flex flex-row gap-2 overflow-x-auto snap-x snap-mandatory pb-2 -mx-1 px-1 [scrollbar-width:thin] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-thumb]:bg-slate-300 [&::-webkit-scrollbar-thumb]:rounded-full">
            {items.map((p, idx) => (
              <InstamartProductTile
                key={p.product_id || `${p.name}-${idx}`}
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

const InstamartCartItemRow: React.FC<{
  item: InstamartCartItem;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ item, onQuickPrompt, disabled }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const theme = themeFor('instamart');
  const qty = typeof item.quantity === 'number' && item.quantity > 0 ? item.quantity : 1;
  const lineTotal = typeof item.price_inr === 'number' ? item.price_inr * qty : null;
  const lineTotalLabel = formatPriceFromInr(lineTotal ?? undefined);
  const showImage = !!item.image_url && !imageBroken;

  const setQty = (newQty: number) => {
    if (!onQuickPrompt || disabled) return;
    const name = item.name || 'this item';
    if (newQty === 0) {
      onQuickPrompt(`Remove ${name} from my Instamart cart.`);
    } else {
      onQuickPrompt(`Set the quantity of ${name} in my Instamart cart to ${newQty}.`);
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
        {item.quantity_description && (
          <p className="text-[11.5px] text-slate-400 mt-0.5">{item.quantity_description}</p>
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

export const InstamartCartCard: React.FC<{
  cart: InstamartCart;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ cart, onQuickPrompt, disabled }) => {
  if (!cart) return null;
  const theme = themeFor('instamart');
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
            <p className="text-[13px] font-semibold text-slate-600">Your Instamart cart is empty.</p>
            <p className="text-[11px] text-slate-400 mt-1">
              {cart.summary_text || 'Add some groceries to get started.'}
            </p>
          </div>
        ) : (
          <>
            <div>
              {items.map((it, idx) => (
                <InstamartCartItemRow
                  key={it.spin_id || `${it.name}-${idx}`}
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
