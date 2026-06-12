/**
 * Floating cart dock — commerce state that doesn't scroll away. Shows a
 * collapsed pill (item count · total · provider dot) whenever any session
 * cart is non-empty, expanding to a snapshot with the provider's checkout
 * prompt wired through onQuickPrompt (same single-source-of-truth routing
 * the cards use). Reads the session carts the app already round-trips.
 */

import React, { useState } from 'react';
import { ShoppingCart, ChevronDown, X } from 'lucide-react';
import type { InstamartCart, SessionCart, ZeptoCart } from '../types';

interface CartDockProps {
  foodCart?: SessionCart | null;
  /** Provider of the turn that produced the food cart (zomato / swiggy_food). */
  foodCartProvider?: string | null;
  zeptoCart?: ZeptoCart | null;
  instamartCart?: InstamartCart | null;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}

interface DockEntry {
  key: string;
  label: string;
  color: string;
  title: string;
  count: number;
  totalInr: number | null;
  lines: { name: string; qty: number; priceInr: number | null }[];
  checkoutPrompt: string;
}

const paiseToInr = (paise: number | null | undefined): number | null =>
  typeof paise === 'number' && Number.isFinite(paise) ? Math.round(paise) / 100 : null;

const buildEntries = (props: CartDockProps): DockEntry[] => {
  const entries: DockEntry[] = [];
  const { foodCart, foodCartProvider, zeptoCart, instamartCart } = props;

  if (foodCart && foodCart.cart_id && Array.isArray(foodCart.items) && foodCart.items.length > 0) {
    const isSwiggy = foodCartProvider === 'swiggy_food' || foodCartProvider === 'swiggy';
    entries.push({
      key: 'food',
      label: isSwiggy ? 'Swiggy' : foodCartProvider === 'zomato' ? 'Zomato' : 'Food',
      color: isSwiggy ? '#FC8019' : foodCartProvider === 'zomato' ? '#E23744' : '#475569',
      title: String(foodCart.restaurant_name || 'Your cart'),
      count: foodCart.items.reduce((n, it) => n + (it.quantity || 1), 0),
      totalInr: typeof foodCart.final_amount === 'number' ? foodCart.final_amount : null,
      lines: foodCart.items.slice(0, 4).map((it) => ({
        name: it.item_name,
        qty: it.quantity || 1,
        priceInr: it.discounted_price ?? it.price ?? null,
      })),
      checkoutPrompt: isSwiggy
        ? 'Checkout my Swiggy cart and place the order.'
        : 'Checkout my Zomato cart and place the order.',
    });
  }

  if (zeptoCart && !zeptoCart.is_empty && Array.isArray(zeptoCart.items) && zeptoCart.items.length > 0) {
    entries.push({
      key: 'zepto',
      label: 'Zepto',
      color: '#7B2CFA',
      title: 'Zepto basket',
      count: zeptoCart.items.reduce((n, it) => n + (it.quantity || 1), 0),
      totalInr: paiseToInr(zeptoCart.grand_total_paise),
      lines: zeptoCart.items.slice(0, 4).map((it: any) => ({
        name: String(it.name || it.product_name || 'Item'),
        qty: it.quantity || 1,
        priceInr: paiseToInr(it.price_paise) ?? (typeof it.price_inr === 'number' ? it.price_inr : null),
      })),
      checkoutPrompt: 'Place my Zepto order.',
    });
  }

  if (
    instamartCart
    && !instamartCart.is_empty
    && Array.isArray(instamartCart.items)
    && instamartCart.items.length > 0
  ) {
    entries.push({
      key: 'instamart',
      label: 'Instamart',
      color: '#E8632F',
      title: 'Instamart basket',
      count: instamartCart.items.reduce((n, it) => n + (it.quantity || 1), 0),
      totalInr: paiseToInr(instamartCart.grand_total_paise),
      lines: instamartCart.items.slice(0, 4).map((it) => ({
        name: String(it.name || 'Item'),
        qty: it.quantity || 1,
        priceInr: typeof it.price_inr === 'number' ? it.price_inr : null,
      })),
      checkoutPrompt: 'Place my Instamart order.',
    });
  }

  return entries;
};

const inr = (value: number | null): string => (value === null ? '' : `₹${Math.round(value)}`);

export const CartDock: React.FC<CartDockProps> = (props) => {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const entries = buildEntries(props);
  if (entries.length === 0 || dismissed) return null;

  return (
    <div className="fixed right-4 md:right-6 bottom-[calc(var(--safe-bottom)+7rem)] md:bottom-32 z-40 flex flex-col items-end gap-2">
      {entries.map((entry) => {
        const open = openKey === entry.key;
        return (
          <div key={entry.key} className="flex flex-col items-end gap-2">
            {open && (
              <div className="w-60 bg-white border-[1.5px] border-[#221b2e] rounded-2xl p-3.5 shadow-[0_18px_40px_-18px_rgba(34,27,46,0.4)] animate-fade-in">
                <div className="flex items-center gap-2 text-[11.5px] font-extrabold text-[#221b2e]">
                  <span className="w-2 h-2 rounded-full" style={{ background: entry.color }} />
                  <span className="truncate">{entry.title} — {entry.label}</span>
                  <button
                    type="button"
                    onClick={() => setOpenKey(null)}
                    className="ml-auto text-[#a89f95] hover:text-[#221b2e] cursor-pointer"
                    aria-label="Collapse cart"
                  >
                    <ChevronDown size={14} />
                  </button>
                </div>
                <div className="mt-2 flex flex-col gap-1.5">
                  {entry.lines.map((line, idx) => (
                    <div key={idx} className="flex justify-between gap-2 text-[11.5px] text-[#4a4458]">
                      <span className="truncate">{line.qty} × {line.name}</span>
                      <span className="shrink-0 font-semibold">{inr(line.priceInr)}</span>
                    </div>
                  ))}
                  {entry.count > entry.lines.length && (
                    <div className="text-[10.5px] text-[#a89f95]">+ more items…</div>
                  )}
                </div>
                {entry.totalInr !== null && (
                  <div className="flex justify-between border-t border-dashed border-[#ddd0bf] mt-2.5 pt-2 text-[12.5px] font-extrabold text-[#221b2e]">
                    <span>To pay</span>
                    <span>{inr(entry.totalInr)}</span>
                  </div>
                )}
                <button
                  type="button"
                  disabled={props.disabled}
                  onClick={() => props.onQuickPrompt?.(entry.checkoutPrompt)}
                  className="btn-ink w-full mt-3 text-white text-[12px] font-bold rounded-full py-2 disabled:opacity-40 cursor-pointer"
                >
                  Checkout →
                </button>
              </div>
            )}
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => setDismissed(true)}
                className="w-6 h-6 rounded-full bg-white/80 border border-[#ece3d6] text-[#a89f95] hover:text-[#221b2e] flex items-center justify-center cursor-pointer"
                title="Hide cart dock"
                aria-label="Hide cart dock"
              >
                <X size={11} />
              </button>
              <button
                type="button"
                onClick={() => setOpenKey(open ? null : entry.key)}
                className="flex items-center gap-2 bg-[#221b2e] text-white rounded-full pl-3.5 pr-4 py-2.5 text-[12px] font-bold shadow-[0_14px_30px_-12px_rgba(34,27,46,0.5)] hover:bg-[#14101d] transition-colors cursor-pointer"
              >
                <ShoppingCart size={14} />
                <span
                  className="rounded-full text-[10px] font-extrabold px-1.5 py-0.5"
                  style={{ background: entry.color }}
                >
                  {entry.count}
                </span>
                {entry.totalInr !== null && <span>{inr(entry.totalInr)}</span>}
                <span className="opacity-70">·</span>
                <span>{open ? 'Hide' : 'Cart'}</span>
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
};
