
import React, { useState } from 'react';
import { Check } from 'lucide-react';
import { FOOD_CATEGORY_GROUPS, type FoodCategory } from '../data/foodCategories';

interface FoodCategoryCardProps {
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}

const TILE_GRADIENTS: Record<string, string> = {
  'north-indian': 'linear-gradient(135deg, #fff1e6 0%, #ffe3cf 100%)',
  biryani: 'linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%)',
  pizza: 'linear-gradient(135deg, #fff0ec 0%, #ffd9cf 100%)',
  burger: 'linear-gradient(135deg, #fff8e1 0%, #ffe9b8 100%)',
  noodles: 'linear-gradient(135deg, #fef6ee 0%, #f6e7d6 100%)',
  chinese: 'linear-gradient(135deg, #fbe9e7 0%, #ffe0d6 100%)',
  rolls: 'linear-gradient(135deg, #fef3e2 0%, #fbe3c8 100%)',
  paratha: 'linear-gradient(135deg, #fdf6e3 0%, #f7e9c8 100%)',
  coffee: 'linear-gradient(135deg, #fdf6e3 0%, #f3e6cf 100%)',
  cake: 'linear-gradient(135deg, #fce4ec 0%, #f8d7e3 100%)',
  pasta: 'linear-gradient(135deg, #fff0e6 0%, #ffdcc4 100%)',
  shake: 'linear-gradient(135deg, #fff7e0 0%, #ffe9a8 100%)',
};

const ProviderChip: React.FC<{
  label: string;
  selected: boolean;
  onToggle: () => void;
}> = ({ label, selected, onToggle }) => (
  <button
    type="button"
    onClick={onToggle}
    className={`flex items-center gap-1 px-2 py-1 sm:px-2.5 sm:py-[5px] rounded-full text-[10px] sm:text-[11px] font-semibold border transition-all duration-150 shrink-0 ${
      selected
        ? 'bg-[#fff1e6] border-[#ff8a1e] text-[#c2630f]'
        : 'bg-white border-slate-200 text-slate-400'
    }`}
  >
    {selected && <Check className="w-3 h-3 stroke-[2.5]" />}
    {label}
  </button>
);

const CategoryTile: React.FC<{
  category: FoodCategory;
  onClick: () => void;
  disabled?: boolean;
}> = ({ category, onClick, disabled }) => {
  const [imgFailed, setImgFailed] = useState(false);

  const gradient =
    TILE_GRADIENTS[category.id] ??
    'linear-gradient(135deg, #fff7f0 0%, #fdeee2 100%)';

  const showImage = Boolean(category.image_url) && !imgFailed;

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
        {showImage ? (
          <img
            src={category.image_url}
            alt={category.image_name}
            loading="lazy"
            onError={() => setImgFailed(true)}
            className="w-[80%] h-[80%] object-contain"
          />
        ) : (
          <div className="w-10 h-10 rounded-full bg-white/60" />
        )}
      </div>

      <span className="text-[10px] sm:text-[11px] font-semibold text-[#221b2e] text-center leading-[1.3] whitespace-pre-line w-full">
        {category.label}
      </span>
    </button>
  );
};

export const FoodCategoryCard: React.FC<FoodCategoryCardProps> = ({
  onQuickPrompt,
  disabled,
}) => {
  const [zomato, setZomato] = useState(true);
  const [swiggy, setSwiggy] = useState(true);

  const toggleZomato = () => {
    if (zomato && !swiggy) return;
    setZomato((v) => !v);
  };

  const toggleSwiggy = () => {
    if (swiggy && !zomato) return;
    setSwiggy((v) => !v);
  };

  const buildPrompt = (cat: FoodCategory) => {
    const providers: string[] = [];

    if (zomato) providers.push('Zomato');
    if (swiggy) providers.push('Swiggy Food');

    return `Find ${cat.term} near my saved address on ${providers.join(
      ' and '
    )}`;
  };

  const allCategories = FOOD_CATEGORY_GROUPS.flatMap(
    (group) => group.categories
  );

  return (
    <div className="w-full max-w-full md:max-w-2xl animate-fade-in">
      <div className="bg-white rounded-[18px] border border-[#f0e2d2] shadow-[0_14px_36px_-24px_rgba(80,45,20,0.45)] overflow-hidden px-3 pt-3 pb-4 sm:px-4 sm:pt-4 sm:pb-5">
        <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5 mb-3">
          <p className="text-[12px] sm:text-[13px] font-semibold text-[#221b2e] shrink-0">
            Popular Cuisines
          </p>

          <div className="flex items-center gap-1 sm:gap-1.5 ml-auto">
            <span className="text-[10px] sm:text-[11px] text-slate-400 font-medium shrink-0">
              Order from
            </span>

            <ProviderChip
              label="Zomato"
              selected={zomato}
              onToggle={toggleZomato}
            />

            <ProviderChip
              label="Swiggy"
              selected={swiggy}
              onToggle={toggleSwiggy}
            />
          </div>
        </div>

        <div className="grid grid-cols-3 sm:grid-cols-4 gap-x-3 gap-y-3">
          {allCategories.map((cat) => (
            <CategoryTile
              key={cat.id}
              category={cat}
              disabled={disabled}
              onClick={() => onQuickPrompt?.(buildPrompt(cat))}
            />
          ))}
        </div>
      </div>
    </div>
  );
};

