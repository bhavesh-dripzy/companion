// In-chat grocery category picker card (issue #59).
//
// Rendered as an assistant attachment (`kind: 'grocery_categories'`) after the
// user taps the Groceries quick-action chip. Replaces the old "fire a fixed
// prompt" behaviour: the user picks a category tile and we fan a search out to
// BOTH quick-commerce providers (Instamart + Zepto) through the normal
// onQuickPrompt → /api/agent/query/ flow.
//
// Categories are a curated, hardcoded set (see data/groceryCategories.ts) —
// neither provider exposes a category-listing tool, so the storefront-style
// grid lives in the frontend. Each tile shows a representative product image
// when we have one, else a tinted emoji fallback.

import React from 'react';
import { ShoppingBasket } from 'lucide-react';
import { GROCERY_CATEGORIES, type GroceryCategory } from '../data/groceryCategories';

interface GroceryCategoryCardProps {
  /** Fires a synthesized user message (search) for the tapped category. */
  onQuickPrompt?: (prompt: string) => void;
  /** True while a turn is in flight — tiles render but don't fire. */
  disabled?: boolean;
}

// Provider-agnostic search wording — the planner fans this out to both
// Swiggy Instamart and Zepto (matches issue #59's multi-provider examples).
const promptFor = (cat: GroceryCategory) =>
  `Search ${cat.term} on Swiggy Instamart and Zepto`;

const GroceryTile: React.FC<{
  category: GroceryCategory;
  onClick: () => void;
  disabled?: boolean;
}> = ({ category, onClick, disabled }) => {
  const [imgFailed, setImgFailed] = React.useState(false);
  const showImage = category.image_url && !imgFailed;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="group flex flex-col items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 active:scale-95 transition-transform duration-150"
    >
      <div className="w-full aspect-square rounded-[14px] border border-[#ece3d6] bg-[#faf5ec] overflow-hidden flex items-center justify-center group-hover:border-[#ddd0bf] group-hover:shadow-[0_8px_20px_-14px_rgba(60,40,30,0.5)] transition-all duration-200">
        {showImage ? (
          <img
            src={category.image_url}
            alt={category.label}
            loading="lazy"
            onError={() => setImgFailed(true)}
            className="w-full h-full object-contain p-1.5"
          />
        ) : (
          <span className="text-3xl leading-none select-none" aria-hidden>
            {category.emoji}
          </span>
        )}
      </div>
      <span className="text-[12px] font-semibold text-[#221b2e] text-center leading-tight">
        {category.label}
      </span>
    </button>
  );
};

export const GroceryCategoryCard: React.FC<GroceryCategoryCardProps> = ({
  onQuickPrompt,
  disabled,
}) => {
  return (
    <div className="mt-3 w-full max-w-full md:max-w-2xl animate-fade-in">
      <div className="bg-white rounded-[18px] border border-[#ece3d6] shadow-[0_14px_36px_-24px_rgba(60,40,30,0.45)] overflow-hidden">
        {/* Header — quick-commerce accent line + title. */}
        <div
          className="h-[3px] w-full"
          style={{ background: 'linear-gradient(90deg, #E8632F 0%, #1a9d6b 50%, #7B2CFA 100%)' }}
        />
        <div className="flex items-center gap-2.5 px-4 pt-3.5 pb-3 border-b border-[#ece3d6]">
          <div className="w-8 h-8 rounded-[11px] bg-[#1a9d6b]/12 text-[#147a53] border border-[#1a9d6b]/20 flex items-center justify-center shrink-0">
            <ShoppingBasket className="w-4 h-4 stroke-[2.2]" />
          </div>
          <div className="min-w-0">
            <p className="font-display text-[14px] font-semibold text-[#221b2e] leading-tight">
              What do you need?
            </p>
            <p className="text-[11px] text-[#7a7388] font-medium mt-0.5 truncate">
              Pick a category — we'll search Instamart & Zepto
            </p>
          </div>
        </div>

        {/* Tile grid. */}
        <div className="grid grid-cols-4 sm:grid-cols-5 gap-3 px-4 py-4">
          {GROCERY_CATEGORIES.map((cat) => (
            <GroceryTile
              key={cat.id}
              category={cat}
              disabled={disabled}
              onClick={() => onQuickPrompt?.(promptFor(cat))}
            />
          ))}
        </div>
      </div>
    </div>
  );
};
