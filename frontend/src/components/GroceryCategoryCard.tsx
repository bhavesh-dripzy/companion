import React from 'react';
import { ShoppingBasket } from 'lucide-react';
import {
  GROCERY_CATEGORY_GROUPS,
  type GroceryCategory,
  type GroceryCategoryGroup,
} from '../data/groceryCategories';

interface GroceryCategoryCardProps {
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}

const promptFor = (cat: GroceryCategory) =>
  `Search ${cat.term} on Swiggy Instamart and Zepto`;

const CategoryTile: React.FC<{
  category: GroceryCategory;
  onClick: () => void;
  disabled?: boolean;
}> = ({ category, onClick, disabled }) => {
  const [imgFailed, setImgFailed] = React.useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="group flex flex-col items-center gap-2 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 active:scale-95 transition-transform duration-150 w-full"
    >
      <div className="w-full aspect-square flex items-center justify-center rounded-2xl overflow-hidden" style={{ boxShadow: '0 0 0 0.8px #3b82f6' }}>
        {!imgFailed ? (
          <img
            src={category.image_url}
            alt={category.image_name}
            loading="lazy"
            onError={() => setImgFailed(true)}
            className="w-full h-full object-contain"
          />
        ) : (
          <span className="text-3xl select-none" aria-hidden>🛒</span>
        )}
      </div>
      <span className="text-[11.5px] font-semibold text-[#221b2e] text-center leading-[1.3] whitespace-pre-line">
        {category.label}
      </span>
    </button>
  );
};

const CategoryGroup: React.FC<{
  group: GroceryCategoryGroup;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ group, onQuickPrompt, disabled }) => {
  const colClass = group.columns === 4 ? 'grid-cols-4' : 'grid-cols-3';

  return (
    <div>
      <p className="text-[14px] font-bold text-[#221b2e] px-4 pt-4 pb-3">{group.title}</p>
      <div className={`grid ${colClass} gap-x-2.5 gap-y-4 px-4 pb-4`}>
        {group.categories.map((cat) => (
          <CategoryTile
            key={cat.id}
            category={cat}
            disabled={disabled}
            onClick={() => onQuickPrompt?.(promptFor(cat))}
          />
        ))}
      </div>
    </div>
  );
};

export const GroceryCategoryCard: React.FC<GroceryCategoryCardProps> = ({
  onQuickPrompt,
  disabled,
}) => (
  <div className="mt-3 w-full max-w-full md:max-w-2xl animate-fade-in">
    <div className="bg-white rounded-[18px] border border-[#ece3d6] shadow-[0_14px_36px_-24px_rgba(60,40,30,0.45)] overflow-hidden">
      <div
        className="h-[3px] w-full"
        style={{ background: 'linear-gradient(90deg, #E8632F 0%, #1a9d6b 50%, #7B2CFA 100%)' }}
      />
      <div className="flex items-center gap-2.5 px-4 pt-3.5 pb-3 border-b border-[#ece3d6]">
        <div className="w-8 h-8 rounded-[11px] bg-[#1a9d6b]/10 text-[#1a9d6b] border border-[#1a9d6b]/20 flex items-center justify-center shrink-0">
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

      {GROCERY_CATEGORY_GROUPS.map((group, i) => (
        <React.Fragment key={group.title}>
          {i > 0 && <div className="h-[1px] bg-[#f0eae0] mx-4" />}
          <CategoryGroup group={group} onQuickPrompt={onQuickPrompt} disabled={disabled} />
        </React.Fragment>
      ))}
    </div>
  </div>
);
