import React from 'react';
import {
  DISTRICT_CATEGORY_GROUPS,
  type DistrictCategory,
} from '../data/districtCategories';

interface DistrictCategoryCardProps {
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}

const CategoryTile: React.FC<{
  category: DistrictCategory;
  onClick: () => void;
  disabled?: boolean;
}> = ({ category, onClick, disabled }) => {
  const [imgFailed, setImgFailed] = React.useState(false);
  const hasImage = Boolean(category.image_url) && !imgFailed;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="group flex flex-col items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 active:scale-95 transition-transform duration-150 w-full"
    >
      <div
        className="w-full aspect-square flex items-center justify-center rounded-2xl overflow-hidden shadow-[0_2px_8px_rgba(0,0,0,0.05)] group-hover:shadow-[0_4px_12px_rgba(0,0,0,0.1)] transition-shadow duration-150"
        style={{ background: 'linear-gradient(135deg, #fdecef 0%, #f5e7fb 100%)' }}
      >
        {hasImage ? (
          <img
            src={category.image_url}
            alt={category.image_name}
            loading="lazy"
            onError={() => setImgFailed(true)}
            className="w-[80%] h-[80%] object-contain"
          />
        ) : (
          <span className="text-3xl select-none" aria-hidden>{category.emoji}</span>
        )}
      </div>
      <span className="text-[10px] sm:text-[11px] font-semibold text-[#221b2e] text-center leading-[1.3] whitespace-pre-line w-full">
        {category.label}
      </span>
    </button>
  );
};

export const DistrictCategoryCard: React.FC<DistrictCategoryCardProps> = ({
  onQuickPrompt,
  disabled,
}) => {
  return (
    <div className="w-full max-w-full md:max-w-2xl animate-fade-in">
      <div className="bg-white rounded-[18px] border border-[#ece3d6] shadow-[0_14px_36px_-24px_rgba(60,40,30,0.45)] overflow-hidden px-3 pt-3 pb-4 sm:px-4 sm:pt-4 sm:pb-5">
        <div className="flex items-center justify-between gap-x-2 mb-3">
          <p className="text-[12px] sm:text-[13px] font-semibold text-[#221b2e]">Discover on District</p>
          <span className="text-[10px] sm:text-[11px] text-slate-400 font-medium">Near your location</span>
        </div>

        <div className="space-y-3">
          {DISTRICT_CATEGORY_GROUPS.map((group) => (
            <div key={group.title}>
              <p className="text-[10px] sm:text-[11px] font-bold uppercase tracking-wide text-[#c23e5b] mb-1.5">
                {group.title}
              </p>
              <div className="grid grid-cols-4 gap-x-2 gap-y-1.5">
                {group.categories.map((cat) => (
                  <CategoryTile
                    key={cat.id}
                    category={cat}
                    disabled={disabled}
                    onClick={() => onQuickPrompt?.(cat.prompt)}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
