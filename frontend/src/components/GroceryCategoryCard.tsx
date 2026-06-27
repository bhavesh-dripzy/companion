import React, { useEffect, useRef, useState } from 'react';
import { Check, AlertCircle } from 'lucide-react';
import {
  GROCERY_CATEGORY_GROUPS,
  type GroceryCategory,
} from '../data/groceryCategories';
import { useMcpProviders } from '../McpProviderContext';

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
  connected: boolean;
  onClick: () => void;
}> = ({ label, selected, connected, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    title={connected ? undefined : `${label} is not connected — tap to connect`}
    className={`flex items-center gap-1 px-2 py-1 sm:px-2.5 sm:py-[5px] rounded-full text-[10px] sm:text-[11px] font-semibold border transition-all duration-150 shrink-0 ${
      !connected
        ? 'bg-amber-50 border-amber-300 text-amber-600'
        : selected
          ? 'bg-[#eaf6f1] border-[#1a9d6b] text-[#1a9d6b]'
          : 'bg-white border-slate-200 text-slate-400'
    }`}
  >
    {!connected ? (
      // Yellow warning = app not connected; tapping opens its auth flow.
      <AlertCircle className="w-3 h-3 stroke-[2.5]" />
    ) : selected ? (
      <Check className="w-3 h-3 stroke-[2.5]" />
    ) : null}
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
      <span className="text-[10px] sm:text-[11px] font-semibold text-[#221b2e] text-center leading-[1.3] whitespace-pre-line w-full">
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
  const { isConnected, connect } = useMcpProviders();
  const instaConnected = isConnected('swiggy_instamart');
  const zeptoConnected = isConnected('zepto');

  // A provider starts selected only if it's actually connected (a logged-out app
  // is never selected by default).
  const [instamart, setInstamart] = useState(instaConnected);
  const [zepto, setZepto] = useState(zeptoConnected);
  const [expanded, setExpanded] = useState(false);

  // Keep selection in sync with connection: auto-select a provider the moment it
  // connects (e.g. the user just authed Zepto from its chip), and drop it if it
  // disconnects. prev-tracking leaves a user's manual deselect of a still-connected
  // provider untouched.
  const prevInsta = useRef(instaConnected);
  const prevZepto = useRef(zeptoConnected);
  useEffect(() => {
    if (instaConnected && !prevInsta.current) setInstamart(true);
    else if (!instaConnected) setInstamart(false);
    prevInsta.current = instaConnected;
  }, [instaConnected]);
  useEffect(() => {
    if (zeptoConnected && !prevZepto.current) setZepto(true);
    else if (!zeptoConnected) setZepto(false);
    prevZepto.current = zeptoConnected;
  }, [zeptoConnected]);

  // Effective selection = user-selected AND connected (a disconnected app is never searched).
  const effInsta = instamart && instaConnected;
  const effZepto = zepto && zeptoConnected;

  const toggleInstamart = () => {
    if (!instaConnected) return connect('swiggy_instamart'); // not connected → open auth
    if (effInsta && !effZepto) return; // keep at least one provider active
    setInstamart((v) => !v);
  };

  const toggleZepto = () => {
    if (!zeptoConnected) return connect('zepto'); // not connected → open auth
    if (effZepto && !effInsta) return;
    setZepto((v) => !v);
  };

  const buildPrompt = (cat: GroceryCategory): string | null => {
    const providers: string[] = [];
    if (effInsta) providers.push('Swiggy Instamart');
    if (effZepto) providers.push('Zepto');
    if (!providers.length) return null; // nothing connected+selected → don't search
    return `Search ${cat.term} on ${providers.join(' and ')}`;
  };

  const allCategories = GROCERY_CATEGORY_GROUPS.flatMap((g) => g.categories);
  const visibleCategories = expanded ? allCategories : allCategories.slice(0, INITIAL_COUNT);

  return (
    <div className="w-full max-w-full md:max-w-2xl animate-fade-in">
      <div className="bg-white rounded-[18px] border border-[#ece3d6] shadow-[0_14px_36px_-24px_rgba(60,40,30,0.45)] overflow-hidden px-3 pt-3 pb-4 sm:px-4 sm:pt-4 sm:pb-5">
        <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5 mb-3">
          <p className="text-[12px] sm:text-[13px] font-semibold text-[#221b2e] shrink-0">Popular Categories</p>
          <div className="flex items-center gap-1 sm:gap-1.5 ml-auto">
            <span className="text-[10px] sm:text-[11px] text-slate-400 font-medium shrink-0">Search in</span>
            <ProviderChip label="Instamart" selected={effInsta} connected={instaConnected} onClick={toggleInstamart} />
            <ProviderChip label="Zepto" selected={effZepto} connected={zeptoConnected} onClick={toggleZepto} />
          </div>
        </div>

        <div className="grid grid-cols-4 gap-x-2 gap-y-1.5">
          {visibleCategories.map((cat) => (
            <CategoryTile
              key={cat.id}
              category={cat}
              disabled={disabled || (!effInsta && !effZepto)}
              onClick={() => {
                const prompt = buildPrompt(cat);
                if (prompt) onQuickPrompt?.(prompt);
              }}
            />
          ))}
        </div>

        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-3 sm:mt-4 w-full flex items-center justify-center gap-2 py-2 sm:py-2.5 rounded-2xl bg-[#f3eeff] text-[#7B2CFA] text-[12px] sm:text-[13px] font-semibold hover:bg-[#ebe5ff] transition-colors duration-150"
        >
          {expanded ? 'Show less' : 'Explore all categories'}
          <span className="text-base leading-none">{expanded ? '↑' : '→'}</span>
        </button>

      </div>
    </div>
  );
};
