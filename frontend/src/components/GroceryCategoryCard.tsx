import React, { useState } from 'react';
import { Check } from 'lucide-react';
import {
  GROCERY_CATEGORY_GROUPS,
  type GroceryCategory,
} from '../data/groceryCategories';

interface GroceryCategoryCardProps {
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}


const TILE_GRADIENTS: Record<string, string> = {
  vegetables:         'linear-gradient(135deg, #e8f5e9 0%, #f1fce8 100%)',
  fruits:             'linear-gradient(135deg, #fff3e0 0%, #fde8d8 100%)',
  milk:               'linear-gradient(135deg, #e3f2fd 0%, #eef4ff 100%)',
  bread:              'linear-gradient(135deg, #fff8e1 0%, #fdf3d0 100%)',
  atta:               'linear-gradient(135deg, #fce4ec 0%, #fdf6e3 100%)',
  oil:                'linear-gradient(135deg, #fffde7 0%, #fef9c3 100%)',
  sooji:              'linear-gradient(135deg, #fff3e0 0%, #fdf6e3 100%)',
  besan:              'linear-gradient(135deg, #fffde7 0%, #fff8e1 100%)',
  'soft-drinks':      'linear-gradient(135deg, #fce4ec 0%, #ffebee 100%)',
  chicken:            'linear-gradient(135deg, #fff3e0 0%, #fef3e2 100%)',
  fish:               'linear-gradient(135deg, #e0f7fa 0%, #e8f4fd 100%)',
  masala:             'linear-gradient(135deg, #fbe9e7 0%, #fff0ec 100%)',
  tea:                'linear-gradient(135deg, #fdf6e3 0%, #fef9ed 100%)',
  coffee:             'linear-gradient(135deg, #efebe9 0%, #f5ede8 100%)',
  chips:              'linear-gradient(135deg, #fffde7 0%, #fff3e0 100%)',
  'healthy-munchies': 'linear-gradient(135deg, #f1f8e9 0%, #e8f5e9 100%)',
  'ice-cream':        'linear-gradient(135deg, #fce4ec 0%, #f3e5f5 100%)',
  chocolates:         'linear-gradient(135deg, #efebe9 0%, #fbe9e7 100%)',
  biscuits:           'linear-gradient(135deg, #fff8e1 0%, #fffde7 100%)',
  cereals:            'linear-gradient(135deg, #fff3e0 0%, #fdf6e3 100%)',
};

const ProviderChip: React.FC<{
  label: string;
  selected: boolean;
  onToggle: () => void;
}> = ({ label, selected, onToggle }) => (
  <button
    type="button"
    onClick={onToggle}
    className={`flex items-center gap-1 px-2.5 py-[5px] rounded-full text-[11px] font-semibold border transition-all duration-150 ${
      selected
        ? 'bg-[#eaf6f1] border-[#1a9d6b] text-[#1a9d6b]'
        : 'bg-white border-slate-200 text-slate-400'
    }`}
  >
    {selected && <Check className="w-3 h-3 stroke-[2.5]" />}
    {label}
  </button>
);

const CategoryTile: React.FC<{
  category: GroceryCategory;
  onClick: () => void;
  disabled?: boolean;
}> = ({ category, onClick, disabled }) => {
  const [imgFailed, setImgFailed] = React.useState(false);
  const gradient = TILE_GRADIENTS[category.id] ?? 'linear-gradient(135deg, #f7f8fa 0%, #f0f1f3 100%)';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="group flex flex-col items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 active:scale-95 transition-transform duration-150 w-full"
    >
      <div
        className="w-full aspect-square flex items-center justify-center rounded-2xl overflow-hidden shadow-[0_2px_8px_rgba(0,0,0,0.05)] group-hover:shadow-[0_4px_12px_rgba(0,0,0,0.1)] transition-shadow duration-150"
        style={{ background: gradient }}
      >
        {!imgFailed ? (
          <img
            src={category.image_url}
            alt={category.image_name}
            loading="lazy"
            onError={() => setImgFailed(true)}
            className="w-[80%] h-[80%] object-contain"
          />
        ) : (
          <span className="text-3xl select-none" aria-hidden>🛒</span>
        )}
      </div>
      <span className="text-[11px] font-semibold text-[#221b2e] text-center leading-[1.3] whitespace-pre-line w-full">
        {category.label}
      </span>
    </button>
  );
};


const INITIAL_COUNT = 12;

export const GroceryCategoryCard: React.FC<GroceryCategoryCardProps> = ({
  onQuickPrompt,
  disabled,
}) => {
  const [instamart, setInstamart] = useState(true);
  const [zepto, setZepto] = useState(true);
  const [expanded, setExpanded] = useState(false);

  const toggleInstamart = () => {
    if (instamart && !zepto) return;
    setInstamart((v) => !v);
  };

  const toggleZepto = () => {
    if (zepto && !instamart) return;
    setZepto((v) => !v);
  };

  const buildPrompt = (cat: GroceryCategory) => {
    const providers: string[] = [];
    if (instamart) providers.push('Swiggy Instamart');
    if (zepto) providers.push('Zepto');
    return `Search ${cat.term} on ${providers.join(' and ')}`;
  };

  const allCategories = GROCERY_CATEGORY_GROUPS.flatMap((g) => g.categories);
  const visibleCategories = expanded ? allCategories : allCategories.slice(0, INITIAL_COUNT);

  return (
    <div className="w-full max-w-full md:max-w-2xl animate-fade-in">
      <div className="bg-white rounded-[18px] border border-[#ece3d6] shadow-[0_14px_36px_-24px_rgba(60,40,30,0.45)] overflow-hidden px-4 pt-4 pb-5">
        <div className="flex items-center justify-between mb-3">
          <p className="text-[13px] font-semibold text-[#221b2e]">Popular Categories</p>
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-slate-400 font-medium">Search in</span>
            <ProviderChip label="Instamart" selected={instamart} onToggle={toggleInstamart} />
            <ProviderChip label="Zepto" selected={zepto} onToggle={toggleZepto} />
          </div>
        </div>

        <div className="grid grid-cols-4 gap-x-2 gap-y-1.5">
          {visibleCategories.map((cat) => (
            <CategoryTile
              key={cat.id}
              category={cat}
              disabled={disabled}
              onClick={() => onQuickPrompt?.(buildPrompt(cat))}
            />
          ))}
        </div>

        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-4 w-full flex items-center justify-center gap-2 py-2.5 rounded-2xl bg-[#f3eeff] text-[#7B2CFA] text-[13px] font-semibold hover:bg-[#ebe5ff] transition-colors duration-150"
        >
          {expanded ? 'Show less' : 'Explore all categories'}
          <span className="text-base leading-none">{expanded ? '↑' : '→'}</span>
        </button>

      </div>
    </div>
  );
};
