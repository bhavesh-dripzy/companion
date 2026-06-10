/**
 * Cross-provider compare strip — when one turn returns restaurant_cards from
 * two or more providers (e.g. "compare pizza on Zomato and Swiggy"), render a
 * unified head-to-head above the per-provider carousels: top pick, rating,
 * ETA, offer, and cheapest preview dish with a "save ₹N" delta pill. This is
 * the one row no single-provider app can draw.
 */

import React from 'react';
import { ArrowRight, Star, Timer } from 'lucide-react';
import type { Attachment, CardProvider, RestaurantCardEntry } from '../types';

const PROVIDER_META: Record<string, { label: string; color: string }> = {
  zomato: { label: 'Zomato', color: '#E23744' },
  swiggy: { label: 'Swiggy', color: '#FC8019' },
  district: { label: 'District', color: '#7C3AED' },
};

interface ProviderColumn {
  provider: CardProvider;
  label: string;
  color: string;
  top: RestaurantCardEntry;
  cheapest: { name: string; price: number } | null;
}

const cheapestItem = (entries: RestaurantCardEntry[]): { name: string; price: number } | null => {
  let best: { name: string; price: number } | null = null;
  for (const resto of entries) {
    for (const item of resto.items || []) {
      const price = typeof item.price === 'number' ? item.price : item.min_price;
      if (typeof price === 'number' && price > 0 && (!best || price < best.price)) {
        best = { name: item.name, price };
      }
    }
  }
  return best;
};

export const buildCompareColumns = (attachments: Attachment[]): ProviderColumn[] => {
  const byProvider = new Map<string, RestaurantCardEntry[]>();
  for (const att of attachments) {
    if (String(att.kind || '').toLowerCase() !== 'restaurant_cards') continue;
    const provider = String(att.provider || '');
    if (!(provider in PROVIDER_META)) continue;
    const rows = Array.isArray(att.restaurants) ? att.restaurants : [];
    if (rows.length === 0) continue;
    byProvider.set(provider, [...(byProvider.get(provider) || []), ...rows]);
  }
  if (byProvider.size < 2) return [];
  return [...byProvider.entries()].map(([provider, rows]) => ({
    provider: provider as CardProvider,
    label: PROVIDER_META[provider].label,
    color: PROVIDER_META[provider].color,
    top: rows[0],
    cheapest: cheapestItem(rows),
  }));
};

export const CompareStrip: React.FC<{
  columns: ProviderColumn[];
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ columns, onQuickPrompt, disabled }) => {
  if (columns.length < 2) return null;

  const prices = columns.map((c) => c.cheapest?.price).filter((p): p is number => typeof p === 'number');
  const minPrice = prices.length >= 2 ? Math.min(...prices) : null;

  return (
    <div className="mt-2 w-full max-w-xl bg-white border-[1.5px] border-[#221b2e] rounded-2xl overflow-hidden shadow-[0_18px_40px_-24px_rgba(34,27,46,0.35)]">
      <div className="px-4 py-2 text-[10px] font-bold uppercase tracking-[0.14em] text-[#7a7388] border-b border-[#ece3d6] bg-[#fdfaf3]">
        Side by side
      </div>
      <div className="grid" style={{ gridTemplateColumns: `repeat(${columns.length}, 1fr)` }}>
        {columns.map((col, idx) => {
          const wins = minPrice !== null && col.cheapest?.price === minPrice;
          const others = prices.filter((p) => p !== col.cheapest?.price);
          const delta = wins && others.length > 0 ? Math.max(...others) - (col.cheapest?.price || 0) : 0;
          return (
            <div
              key={col.provider}
              className={`px-4 py-3 flex flex-col gap-1.5 ${idx > 0 ? 'border-l border-[#ece3d6]' : ''}`}
            >
              <div className="flex items-center gap-1.5 text-[11px] font-extrabold" style={{ color: col.color }}>
                <span className="w-2 h-2 rounded-full" style={{ background: col.color }} />
                {col.label}
              </div>
              <div className="text-[13px] font-bold text-[#221b2e] truncate">{col.top.name}</div>
              <div className="flex items-center gap-2.5 text-[11px] text-[#7a7388]">
                {typeof col.top.rating === 'number' && (
                  <span className="inline-flex items-center gap-0.5">
                    <Star size={10} className="text-[#ffb347]" fill="#ffb347" /> {col.top.rating}
                  </span>
                )}
                {col.top.eta && (
                  <span className="inline-flex items-center gap-0.5">
                    <Timer size={10} /> {col.top.eta}
                  </span>
                )}
              </div>
              {col.cheapest && (
                <div className="text-[12px] text-[#4a4458]">
                  <span className="truncate">{col.cheapest.name}</span>{' '}
                  <span className={`font-mono font-bold ${wins ? 'text-[#1a9d6b]' : 'text-[#221b2e]'}`}>
                    ₹{Math.round(col.cheapest.price)}
                  </span>
                  {wins && delta > 0 && (
                    <span className="ml-1.5 inline-block bg-[#1a9d6b] text-white text-[9px] font-extrabold rounded-full px-1.5 py-0.5 align-middle">
                      save ₹{Math.round(delta)}
                    </span>
                  )}
                </div>
              )}
              {col.top.res_offer && (
                <div className="text-[10.5px] font-bold text-[#c2630f]">{col.top.res_offer}</div>
              )}
              <button
                type="button"
                disabled={disabled}
                onClick={() => onQuickPrompt?.(`Show me the menu of ${col.top.name} on ${col.label}.`)}
                className="mt-1 inline-flex items-center gap-1 self-start text-[11px] font-bold text-[#221b2e] bg-[#fdfaf3] border border-[#ddd0bf] rounded-full px-2.5 py-1 hover:bg-[#221b2e] hover:text-white transition-colors disabled:opacity-40 cursor-pointer"
              >
                Menu <ArrowRight size={10} />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
};
