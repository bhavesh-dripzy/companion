import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import { CompareStrip, buildCompareColumns } from './components/CompareStrip';
import type {
  Attachment,
  CardProvider,
  MenuCardItem,
  Message,
  RestaurantCardEntry,
  RestaurantCardItem,
  SessionAddress,
  SessionCart,
} from './types';
import { cn } from './utils';
import {
  ArtistCardGrid,
  DiningCardGrid,
  EventCardGrid,
  MovieCardGrid,
  ReservationCardComponent,
  ShowCardGrid,
  TheatreCardGrid,
  VenueCardGrid,
} from './components/DistrictCards';
import { ZeptoCartCard, ZeptoProductCardGrid } from './components/ZeptoCards';
import { InstamartCartCard, InstamartProductCardGrid } from './components/InstamartCards';
import {
  KiteGttCard,
  KiteHoldingsCard,
  KiteInstrumentGrid,
  KiteMarginCard,
  KiteMfHoldingsCard,
  KiteOpenOrdersCard,
  KiteOrderTicketCard,
  KitePositionsCard,
  KiteTradesCard,
} from './components/KiteCards';
import {
  CouponCardGrid,
  InfoCardView,
  OrderCardGrid,
  ReviewCardGrid,
  SlotCardGrid,
  TrackingCardView,
} from './components/ResultCards';
import {
  FlightCardGrid,
  HotelCardGrid,
  PlaceCardGrid,
  ShopProductCardGrid,
  VideoCardGrid,
} from './components/SerpCards';
import {
  ExternalLink,
  MapPin,
  ChevronDown,
  ChevronUp,
  Star,
  UtensilsCrossed,
  Clock,
  TrendingUp,
  RefreshCw,
  ThumbsUp,
  ThumbsDown,
  ShoppingBag,
  Info,
  Plus,
  Eye,
  Menu as MenuIcon,
  CheckCircle2,
  Layers,
  Receipt,
  ChevronRight,
} from 'lucide-react';

interface ChatLogProps {
  messages: Message[];
  isThinking: boolean;
  sessionAddresses: SessionAddress[];
  onSelectAddress: (addr: SessionAddress) => void;
  searchQuery: string;
  // Send a synthesized chat message on behalf of the user (e.g. when
  // they tap a quick-action button on a restaurant card). The receiving
  // component must be disabled while a turn is in flight; we surface
  // that via `disabled` so the popover renders click-but-no-op buttons
  // greyed instead of accidentally double-firing.
  onQuickPrompt?: (prompt: string) => void;
  quickPromptDisabled?: boolean;
}

// One option in a `QuickActionPopover`. `label` is the button text,
// `prompt` is the literal user message that gets routed through
// `handleSendMessage` as if the user typed it themselves — this keeps
// the agent's intent-routing logic the single source of truth.
interface QuickAction {
  label: string;
  prompt: string;
  hint?: string;
  icon?: React.ReactNode;
  tone?: 'default' | 'primary';
}

// Small floating action menu anchored to a clicked element. Rendered via
// portal so card-level `overflow-hidden` and grid clipping never cut it
// off, and the click-outside backdrop traps stray clicks so the menu
// dismisses cleanly without leaking pointer events through to anything
// else in the chat (avatars, message bubbles, the input).
const QuickActionPopover: React.FC<{
  anchor: DOMRect;
  actions: QuickAction[];
  title?: string;
  subtitle?: string;
  disabled?: boolean;
  onSelect: (action: QuickAction) => void;
  onClose: () => void;
}> = ({ anchor, actions, title, subtitle, disabled, onSelect, onClose }) => {
  // Dismiss on Escape — standard popover affordance.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const POPOVER_WIDTH = 240;
  const margin = 8;
  // Default placement: directly under the trigger, left-aligned. Clamp
  // horizontally so the popover never spills off the viewport, and flip
  // above when there isn't room below (long restaurant cards near the
  // bottom of the chat scroll).
  const spaceBelow = window.innerHeight - anchor.bottom;
  const placeAbove = spaceBelow < 180 && anchor.top > 180;
  const top = placeAbove ? anchor.top - margin : anchor.bottom + margin;
  const rawLeft = anchor.left;
  const left = Math.max(
    margin,
    Math.min(rawLeft, window.innerWidth - POPOVER_WIDTH - margin),
  );

  return createPortal(
    <>
      <div
        className="fixed inset-0 z-[60]"
        onClick={onClose}
        onContextMenu={(e) => {
          e.preventDefault();
          onClose();
        }}
      />
      <div
        role="menu"
        className="fixed z-[61] bg-white rounded-2xl shadow-2xl border border-slate-200 p-2 animate-fade-in"
        style={{
          top,
          left,
          width: POPOVER_WIDTH,
          transform: placeAbove ? 'translateY(-100%)' : undefined,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {(title || subtitle) && (
          <div className="px-2 pt-1 pb-2 border-b border-slate-100 mb-1">
            {title && (
              <div className="text-[11px] font-extrabold text-slate-900 truncate leading-tight">
                {title}
              </div>
            )}
            {subtitle && (
              <div className="text-[10px] text-slate-500 truncate leading-tight mt-0.5">
                {subtitle}
              </div>
            )}
          </div>
        )}
        <div className="flex flex-col">
          {actions.map((a) => (
            <button
              key={a.label}
              type="button"
              role="menuitem"
              disabled={disabled}
              onClick={() => onSelect(a)}
              className={cn(
                'w-full text-left px-2.5 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer',
                a.tone === 'primary'
                  ? 'text-white bg-[#E23744] hover:bg-[#c92e3a]'
                  : 'text-slate-800 hover:bg-slate-100',
                disabled && 'opacity-50 cursor-not-allowed pointer-events-none',
              )}
            >
              {a.icon && (
                <span className="shrink-0 w-4 h-4 flex items-center justify-center">
                  {a.icon}
                </span>
              )}
              <span className="flex-1 min-w-0">
                <span className="block text-[12px] font-bold leading-tight">{a.label}</span>
                {a.hint && (
                  <span
                    className={cn(
                      'block text-[10px] mt-0.5 leading-tight truncate',
                      a.tone === 'primary' ? 'text-white/80' : 'text-slate-500',
                    )}
                  >
                    {a.hint}
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>
      </div>
    </>,
    document.body,
  );
};

// Detect when the assistant is prompting the user to choose / switch their
// delivery address. Same keyword heuristic the previous skin used — the
// backend doesn't ship a structured signal for "awaiting address choice".
const ADDRESS_WORD = /\b(address|addresses|adderess|adderesses)\b/i;
const ADDRESS_PROMPT_HINT = /\?|which|select|pick|choose|switch|change|kaunsa|kaunsi|konsa|konsi|batao|prefer|use a different|use my/i;

export const looksLikeAddressPrompt = (text: string): boolean => {
  if (!text) return false;
  return ADDRESS_WORD.test(text) && ADDRESS_PROMPT_HINT.test(text);
};

export const AddressChoices: React.FC<{
  addresses: SessionAddress[];
  onSelect: (addr: SessionAddress) => void;
}> = ({ addresses, onSelect }) => {
  const [expanded, setExpanded] = useState(false);

  const valid = addresses
    .filter((a): a is SessionAddress => !!a && typeof a === 'object')
    .filter((a) => !!(a.location_name || a.address_id));

  if (valid.length === 0) return null;

  const visible = expanded ? valid : valid.slice(0, 3);
  const remaining = valid.length - visible.length;

  return (
    <div className="mt-2 flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {visible.map((addr, idx) => (
          <button
            key={addr.address_id || idx}
            onClick={() => onSelect(addr)}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-50 hover:border-slate-300 shadow-sm transition-all"
          >
            <MapPin size={12} className="text-slate-500" />
            <span className="max-w-[220px] truncate">{addr.location_name || addr.address_id}</span>
          </button>
        ))}
        {!expanded && remaining > 0 && (
          <button
            onClick={() => setExpanded(true)}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full bg-slate-100/70 border border-slate-200 text-xs font-semibold text-slate-500 hover:text-slate-800 hover:border-slate-300 transition-all"
          >
            <ChevronDown size={12} />
            {remaining} more
          </button>
        )}
      </div>
    </div>
  );
};

const VEG_DOT_COLOR: Record<NonNullable<MenuCardItem['veg_kind']>, string> = {
  veg: 'bg-green-500 ring-green-700/40',
  non_veg: 'bg-red-500 ring-red-700/40',
  egg: 'bg-amber-400 ring-amber-700/40',
};

// Per-provider visual + copy theme. Keeps every card-rendering component
// pure-presentational: pass `provider` (or undefined for legacy turns)
// and read the theme once. Adding a third provider later means appending
// one block here — not editing every card.
//
// Colours intentionally match each brand's official mark:
//   * Zomato — `#E23744` red (CDC system, "Zomato Red")
//   * Swiggy — `#FC8019` orange ("Swiggy Orange")
// A neutral slate theme acts as a safe default for unknown/missing
// providers (older chat history pre-stamp, future providers).
interface ProviderTheme {
  label: string;
  // Solid brand colour used for badges, primary CTAs, accent borders.
  // We keep it as raw HEX so Tailwind's arbitrary-value syntax can use
  // it everywhere consistently (`bg-[#E23744]`, `text-[#E23744]`, …).
  brandHex: string;
  // Subtle background tint shown behind the cart-header band — chosen
  // light enough to read white text + dark icons on the same card.
  brandTintBg: string;
  brandTintBorder: string;
  // Copy used for the "Order now" cart CTA so the prompt the user fires
  // names the correct provider — the backend's intent router pins the
  // turn to the right MCP based on this exact substring ("Swiggy" vs
  // "Zomato"), so getting the wording right matters more than aesthetics.
  checkoutPrompt: string;
  cartHeaderTitle: string;
  // Tailwind ring colour for the active-item highlight on restaurant
  // cards. Matches `brandHex` family but at a softer opacity.
  activeRingClass: string;
}

const PROVIDER_THEMES: Record<CardProvider, ProviderTheme> = {
  zomato: {
    label: 'Zomato',
    brandHex: '#E23744',
    brandTintBg: 'bg-[#E23744]/5',
    brandTintBorder: 'border-red-100/80',
    checkoutPrompt: 'Checkout my Zomato cart and place the order.',
    cartHeaderTitle: 'Zomato Checkout API',
    activeRingClass: 'bg-[#E23744]/10 ring-1 ring-[#E23744]/30',
  },
  swiggy: {
    label: 'Swiggy',
    brandHex: '#FC8019',
    brandTintBg: 'bg-[#FC8019]/5',
    brandTintBorder: 'border-orange-100/80',
    checkoutPrompt: 'Checkout my Swiggy cart and place the order.',
    cartHeaderTitle: 'Swiggy Checkout API',
    activeRingClass: 'bg-[#FC8019]/10 ring-1 ring-[#FC8019]/30',
  },
  district: {
    label: 'District',
    brandHex: '#7C3AED',
    brandTintBg: 'bg-[#7C3AED]/5',
    brandTintBorder: 'border-violet-100/80',
    // District doesn't ship a cart / checkout; the equivalent action is
    // "confirm reservation" — but we don't auto-trigger that from a card.
    checkoutPrompt: 'Confirm my District reservation.',
    cartHeaderTitle: 'District Booking',
    activeRingClass: 'bg-[#7C3AED]/10 ring-1 ring-[#7C3AED]/30',
  },
  // Zepto brand palette is a bright violet/purple ("Zepto Purple"
  // #7B2CFA — close enough to District's #7C3AED that we shift a few
  // hex steps to keep the two visually distinct: District is desaturated
  // royal, Zepto leans warmer / pinker).
  zepto: {
    label: 'Zepto',
    brandHex: '#7B2CFA',
    brandTintBg: 'bg-[#7B2CFA]/5',
    brandTintBorder: 'border-violet-100/80',
    checkoutPrompt: 'Place my Zepto order.',
    cartHeaderTitle: 'Zepto Grocery',
    activeRingClass: 'bg-[#7B2CFA]/10 ring-1 ring-[#7B2CFA]/30',
  },
  // Instamart uses Swiggy's brand orange but warmer/darker so the chat
  // scroll still telegraphs that this is the grocery surface (not Swiggy
  // food). Swiggy main = #FC8019; Instamart = #F7BB1F (Swiggy's q-commerce
  // accent) — high enough contrast and stays recognisable.
  instamart: {
    label: 'Instamart',
    brandHex: '#E8632F',
    brandTintBg: 'bg-[#E8632F]/5',
    brandTintBorder: 'border-orange-100/80',
    checkoutPrompt: 'Place my Instamart order.',
    cartHeaderTitle: 'Swiggy Instamart',
    activeRingClass: 'bg-[#E8632F]/10 ring-1 ring-[#E8632F]/30',
  },
  // Kite (Zerodha) uses Zerodha's brand blue (#387ED1) — visually distinct
  // from every food / grocery provider so trading turns stand out in the
  // chat scroll. ``checkoutPrompt`` doubles as the order-ticket confirm
  // string (the safety-critical order ticket card fires it verbatim).
  kite: {
    label: 'Kite',
    brandHex: '#387ED1',
    brandTintBg: 'bg-[#387ED1]/5',
    brandTintBorder: 'border-blue-100/80',
    checkoutPrompt: 'Confirm and place the order.',
    cartHeaderTitle: 'Zerodha Kite',
    activeRingClass: 'bg-[#387ED1]/10 ring-1 ring-[#387ED1]/30',
  },
  serp: {
    label: 'Search',
    brandHex: '#2b2f72',
    brandTintBg: 'bg-[#2b2f72]/5',
    brandTintBorder: 'border-[#2b2f72]/20',
    checkoutPrompt: '',
    cartHeaderTitle: 'Search results',
    activeRingClass: 'bg-[#2b2f72]/10 ring-1 ring-[#2b2f72]/30',
  },
};

const NEUTRAL_THEME: ProviderTheme = {
  label: 'Food',
  brandHex: '#475569', // slate-600
  brandTintBg: 'bg-slate-100',
  brandTintBorder: 'border-slate-200',
  checkoutPrompt: 'Checkout my cart and place the order.',
  cartHeaderTitle: 'Checkout',
  activeRingClass: 'bg-slate-100 ring-1 ring-slate-300',
};

export const themeFor = (provider: CardProvider | undefined): ProviderTheme =>
  (provider && PROVIDER_THEMES[provider]) || NEUTRAL_THEME;

const formatPriceINR = (value: number | undefined): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null;
  return `₹${Math.round(value)}`;
};

const MenuCard: React.FC<{ item: MenuCardItem }> = ({ item }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const price = formatPriceINR(item.price);
  const disc = formatPriceINR(item.discounted_price);
  const hasDiscount = !!disc && !!price && disc !== price;
  const showRating = typeof item.rating === 'number' && item.rating > 0;
  const showImage = !!item.image_url && !imageBroken;

  return (
    <div className="snap-start shrink-0 w-40 flex flex-col gap-1.5 p-2 bg-white/90 border border-slate-200/80 rounded-xl shadow-sm hover:shadow-md hover:border-slate-300 transition-all">
      <div className="relative w-full aspect-square rounded-lg overflow-hidden bg-slate-100 border border-slate-200/70">
        {showImage ? (
          <img
            src={item.image_url}
            alt={item.name}
            loading="lazy"
            onError={() => setImageBroken(true)}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-slate-400">
            <UtensilsCrossed size={22} strokeWidth={1.5} />
          </div>
        )}
        {item.veg_kind && (
          <div className="absolute top-1.5 left-1.5 w-3.5 h-3.5 rounded-sm bg-white/90 backdrop-blur-sm flex items-center justify-center ring-1 ring-slate-200">
            <span
              className={cn('block w-1.5 h-1.5 rounded-full ring-1', VEG_DOT_COLOR[item.veg_kind])}
              aria-label={item.veg_kind === 'non_veg' ? 'Non-vegetarian' : item.veg_kind === 'egg' ? 'Contains egg' : 'Vegetarian'}
            />
          </div>
        )}
        {showRating && (
          <div className="absolute bottom-1.5 right-1.5 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md bg-white/90 backdrop-blur-sm text-[10px] font-semibold text-slate-800 border border-slate-200/70">
            <Star size={9} className="fill-amber-400 text-amber-500" />
            {item.rating!.toFixed(1)}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-0.5 min-w-0">
        <div className="text-[12px] font-semibold text-slate-900 leading-snug line-clamp-2">{item.name}</div>
        <div className="flex items-baseline gap-1.5 mt-0.5">
          {hasDiscount ? (
            <>
              <span className="text-[13px] font-bold text-slate-900">{disc}</span>
              <span className="text-[10px] text-slate-400 line-through">{price}</span>
            </>
          ) : (
            price && <span className="text-[13px] font-bold text-slate-900">{price}</span>
          )}
        </div>
      </div>
    </div>
  );
};

const MenuCardGrid: React.FC<{ attachment: Attachment }> = ({ attachment }) => {
  const items = Array.isArray(attachment.items) ? attachment.items : [];
  if (items.length === 0) return null;
  const theme = themeFor(attachment.provider);
  const heading = attachment.restaurant_name ? `Menu — ${attachment.restaurant_name}` : 'Menu';
  const subhead =
    Array.isArray(attachment.categories) && attachment.categories.length > 0
      ? attachment.categories.join(' • ')
      : null;

  return (
    <div className="mt-1 w-full max-w-full">
      <div className="flex items-baseline justify-between mb-2 px-1">
        <div className="flex items-center gap-1.5 min-w-0">
          {attachment.provider && (
            <span
              className="text-white font-bold text-[9px] tracking-wider px-1.5 py-0.5 rounded uppercase shrink-0"
              style={{ backgroundColor: theme.brandHex }}
            >
              {theme.label}
            </span>
          )}
          <div className="text-xs font-bold uppercase tracking-wider text-slate-500 truncate">{heading}</div>
        </div>
        {subhead && (
          <div className="text-[10px] text-slate-400 truncate ml-3 max-w-[55%]">{subhead}</div>
        )}
      </div>
      <div
        className="flex flex-row gap-2.5 overflow-x-auto snap-x snap-mandatory pb-2 -mx-1 px-1 [scrollbar-width:thin] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-thumb]:bg-slate-300 [&::-webkit-scrollbar-thumb]:rounded-full"
      >
        {items.map((item, idx) => (
          <MenuCard key={item.variant_id || item.item_id || `${item.name}-${idx}`} item={item} />
        ))}
      </div>
    </div>
  );
};

// One featured dish row inside a restaurant card. Compact: veg dot + name
// + price on the left, square thumbnail on the right when available.
// The whole row is a button when an `onClick` is provided so tapping
// anywhere on it opens the item-level quick-action popover (Add to cart
// / See details).
const RestaurantFeaturedItem: React.FC<{
  item: RestaurantCardItem;
  onClick?: (anchor: DOMRect) => void;
  active?: boolean;
  activeRingClass?: string;
}> = ({ item, onClick, active, activeRingClass }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const price = formatPriceINR(item.price);
  const showImage = !!item.image_url && !imageBroken;

  const handleClick = (e: React.MouseEvent<HTMLElement>) => {
    if (!onClick) return;
    onClick(e.currentTarget.getBoundingClientRect());
  };

  const inner = (
    <>
      <div className="min-w-0 flex-1 flex flex-col gap-0.5">
        <div className="flex items-start gap-1.5">
          {item.veg_kind && (
            <span
              className={cn(
                'mt-[3px] w-3 h-3 border p-[1.5px] flex items-center justify-center rounded-sm shrink-0',
                item.veg_kind === 'veg' ? 'border-emerald-600' : 'border-red-600'
              )}
            >
              <span
                className={cn(
                  'block w-1 h-1 rounded-full',
                  item.veg_kind === 'veg' ? 'bg-emerald-600' : 'bg-red-600'
                )}
              />
            </span>
          )}
          <span className="text-[11px] font-semibold text-slate-800 leading-snug line-clamp-2 text-left">
            {item.name}
          </span>
        </div>
        {price && (
          <span className="ml-[18px] text-[11px] font-bold text-slate-900">{price}</span>
        )}
      </div>
      {showImage && (
        <div className="w-10 h-10 rounded-md overflow-hidden bg-slate-100 shrink-0 border border-slate-200/70">
          <img
            src={item.image_url}
            alt={item.name}
            loading="lazy"
            onError={() => setImageBroken(true)}
            className="w-full h-full object-cover"
            referrerPolicy="no-referrer"
          />
        </div>
      )}
    </>
  );

  if (!onClick) {
    return (
      <div className="flex items-center gap-2 px-2 py-1.5">
        {inner}
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={handleClick}
      className={cn(
        'w-full flex items-center gap-2 px-2 py-1.5 text-left transition-colors cursor-pointer',
        active
          ? (activeRingClass || 'bg-slate-100 ring-1 ring-slate-300')
          : 'hover:bg-slate-50/80',
      )}
      title="Tap for options"
    >
      {inner}
    </button>
  );
};

const formatDistanceKm = (value: number | undefined): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
  return `${value} km`;
};

// One restaurant tile. Image header with rating + offer ribbon, meta row
// (distance + ETA), then a collapsible list of featured dishes pulled
// straight from Zomato's keyword-search preview.
// Build the prompt strings the agent will receive when the user taps a
// quick action. Centralised so the wording stays consistent — these
// strings are intercepted by the existing intent router exactly as if
// the user typed them, no special path on the backend.
// Quick-action prompts include the provider name ("on Swiggy" / "on
// Zomato") so the backend orchestrator's stickiness check pins the turn
// to the right MCP. Without the suffix, a Swiggy card click followed by
// an ambiguous query could silently drift to the default Zomato planner.
const providerSuffix = (provider: CardProvider | undefined): string => {
  if (provider === 'swiggy') return ' on Swiggy';
  if (provider === 'zomato') return ' on Zomato';
  return '';
};

const buildItemActionPrompt = (
  kind: 'add' | 'details',
  item: RestaurantCardItem,
  restaurantName: string,
  provider?: CardProvider,
): string => {
  const name = (item.name || '').trim() || 'this item';
  const resto = (restaurantName || '').trim();
  const restoSuffix = resto ? ` from ${resto}` : '';
  const psuffix = providerSuffix(provider);
  if (kind === 'add') {
    return `Add 1 × ${name}${restoSuffix} to my cart${psuffix}.`;
  }
  return `Tell me more about ${name}${restoSuffix}${psuffix} — what's in it, price, and any add-ons.`;
};

const buildRestoActionPrompt = (
  kind: 'full' | 'veg' | 'nonveg',
  restaurantName: string,
  provider?: CardProvider,
): string => {
  const resto = (restaurantName || '').trim() || 'this restaurant';
  const psuffix = providerSuffix(provider);
  if (kind === 'veg') return `Show me only the veg items from ${resto}${psuffix}.`;
  if (kind === 'nonveg') return `Show me only the non-veg items from ${resto}${psuffix}.`;
  return `Show me the menu of ${resto}${psuffix}.`;
};

const RestaurantCard: React.FC<{
  restaurant: RestaurantCardEntry;
  provider?: CardProvider;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ restaurant, provider, onQuickPrompt, disabled }) => {
  const theme = themeFor(provider);
  const items = Array.isArray(restaurant.items) ? restaurant.items : [];
  const hasItems = items.length > 0;
  const [imageBroken, setImageBroken] = useState(false);
  const [expanded, setExpanded] = useState(true);
  // Two independent popovers — one for the restaurant header (menu
  // actions) and one for whichever featured item the user tapped. We
  // never want both open at once, so opening one closes the other.
  const [restoAnchor, setRestoAnchor] = useState<DOMRect | null>(null);
  const [itemPopover, setItemPopover] = useState<
    { anchor: DOMRect; item: RestaurantCardItem; key: string } | null
  >(null);

  const interactive = !!onQuickPrompt;
  const showImage = !!restaurant.res_image && !imageBroken;
  const showRating =
    typeof restaurant.rating === 'number' && restaurant.rating > 0;
  const eta = restaurant.eta ? String(restaurant.eta) : null;
  const distance = formatDistanceKm(restaurant.distance_km);
  const offer = restaurant.res_offer ? String(restaurant.res_offer).trim() : '';

  const handleHeaderClick = (e: React.MouseEvent<HTMLElement>) => {
    if (!interactive) return;
    setItemPopover(null);
    setRestoAnchor(e.currentTarget.getBoundingClientRect());
  };

  const handleItemClick = (item: RestaurantCardItem, key: string) =>
    (anchor: DOMRect) => {
      setRestoAnchor(null);
      setItemPopover({ anchor, item, key });
    };

  const fire = (prompt: string) => {
    if (!onQuickPrompt) return;
    onQuickPrompt(prompt);
    setRestoAnchor(null);
    setItemPopover(null);
  };

  return (
    <div className="w-full flex flex-col bg-white/95 border border-slate-200/80 rounded-2xl overflow-hidden shadow-sm hover:shadow-md hover:border-slate-300 transition-all">
      {/* Image header — clickable when interactive, opens the restaurant
          action popover (show full / veg / non-veg menu). We render it
          as a button so it gets keyboard focus + announces as a control,
          but only when `onQuickPrompt` is wired; otherwise it stays a
          plain div so a non-interactive consumer (e.g. read-only history
          replay) doesn't get phantom hover states. */}
      {interactive ? (
        <button
          type="button"
          onClick={handleHeaderClick}
          disabled={disabled}
          className={cn(
            'relative h-28 w-full bg-slate-900 overflow-hidden block text-left',
            disabled
              ? 'cursor-not-allowed opacity-80'
              : 'cursor-pointer hover:brightness-105 transition-[filter]',
          )}
          title="Tap for menu options"
        >
          {showImage ? (
            <img
              src={restaurant.res_image}
              alt={restaurant.name}
              loading="lazy"
              onError={() => setImageBroken(true)}
              className="w-full h-full object-cover opacity-95"
              referrerPolicy="no-referrer"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-slate-500 bg-gradient-to-br from-slate-800 to-slate-700">
              <UtensilsCrossed size={28} strokeWidth={1.5} />
            </div>
          )}
          <div
            className="absolute top-1.5 left-1.5 z-10 text-white font-bold text-[9px] tracking-wider px-1.5 py-0.5 rounded uppercase"
            style={{ backgroundColor: theme.brandHex }}
          >
            {theme.label}
          </div>
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/30 to-transparent flex flex-col justify-end p-2">
            {offer && (
              <span className="self-start mb-1 inline-block bg-amber-400 text-slate-950 font-extrabold text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded-md max-w-full truncate">
                {offer}
              </span>
            )}
            <div className="flex justify-between items-end gap-2">
              <h3 className="text-white font-bold text-[13px] leading-tight truncate drop-shadow-sm flex-1">
                {restaurant.name}
              </h3>
              {showRating && (
                <div className="flex items-center gap-0.5 bg-emerald-600 text-white font-bold text-[11px] px-1.5 py-0.5 rounded shrink-0">
                  <Star size={9} className="fill-white" />
                  <span>{restaurant.rating!.toFixed(1)}</span>
                </div>
              )}
            </div>
          </div>
        </button>
      ) : (
        <div className="relative h-28 w-full bg-slate-900 overflow-hidden">
          {showImage ? (
            <img
              src={restaurant.res_image}
              alt={restaurant.name}
              loading="lazy"
              onError={() => setImageBroken(true)}
              className="w-full h-full object-cover opacity-95"
              referrerPolicy="no-referrer"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-slate-500 bg-gradient-to-br from-slate-800 to-slate-700">
              <UtensilsCrossed size={28} strokeWidth={1.5} />
            </div>
          )}
          <div
            className="absolute top-1.5 left-1.5 z-10 text-white font-bold text-[9px] tracking-wider px-1.5 py-0.5 rounded uppercase"
            style={{ backgroundColor: theme.brandHex }}
          >
            {theme.label}
          </div>
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/30 to-transparent flex flex-col justify-end p-2">
            {offer && (
              <span className="self-start mb-1 inline-block bg-amber-400 text-slate-950 font-extrabold text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded-md max-w-full truncate">
                {offer}
              </span>
            )}
            <div className="flex justify-between items-end gap-2">
              <h3 className="text-white font-bold text-[13px] leading-tight truncate drop-shadow-sm flex-1">
                {restaurant.name}
              </h3>
              {showRating && (
                <div className="flex items-center gap-0.5 bg-emerald-600 text-white font-bold text-[11px] px-1.5 py-0.5 rounded shrink-0">
                  <Star size={9} className="fill-white" />
                  <span>{restaurant.rating!.toFixed(1)}</span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {(distance || eta) && (
        <div className="grid grid-cols-2 text-center border-b border-slate-100 bg-slate-50/60 text-[11px] py-1.5">
          {distance && (
            <div className="flex items-center justify-center gap-1 font-semibold text-slate-600">
              <MapPin size={12} className="text-slate-400" />
              <span>{distance}</span>
            </div>
          )}
          {eta && (
            <div
              className={cn(
                'flex items-center justify-center gap-1 font-semibold text-slate-600',
                distance && 'border-l border-slate-200'
              )}
            >
              <Clock size={12} className="text-slate-400" />
              <span>{eta}</span>
            </div>
          )}
        </div>
      )}

      {hasItems && (
        <>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="w-full text-left px-2.5 py-1.5 bg-white flex items-center justify-between text-slate-600 hover:bg-slate-50 border-b border-slate-100 transition-colors cursor-pointer"
          >
            <span className="text-[10px] font-extrabold text-slate-500 uppercase tracking-wider">
              Top picks ({items.length})
            </span>
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
          {expanded && (
            <div className="divide-y divide-slate-100 max-h-56 overflow-y-auto bg-white">
              {items.map((item, idx) => {
                const key = item.variant_id || item.item_id || `${item.name}-${idx}`;
                return (
                  <RestaurantFeaturedItem
                    key={key}
                    item={item}
                    onClick={interactive ? handleItemClick(item, key) : undefined}
                    active={itemPopover?.key === key}
                    activeRingClass={theme.activeRingClass}
                  />
                );
              })}
            </div>
          )}
        </>
      )}

      {restoAnchor && (
        <QuickActionPopover
          anchor={restoAnchor}
          onClose={() => setRestoAnchor(null)}
          title={restaurant.name}
          subtitle="Browse this restaurant"
          disabled={disabled}
          onSelect={(a) => fire(a.prompt)}
          actions={[
            {
              label: 'Show full menu',
              prompt: buildRestoActionPrompt('full', restaurant.name, provider),
              hint: 'Open the complete menu',
              icon: <MenuIcon size={14} />,
              tone: 'primary',
            },
            {
              label: 'Veg menu only',
              prompt: buildRestoActionPrompt('veg', restaurant.name, provider),
              hint: 'Filter to vegetarian dishes',
              icon: (
                <span className="w-3 h-3 border border-emerald-600 rounded-sm flex items-center justify-center">
                  <span className="w-1 h-1 rounded-full bg-emerald-600" />
                </span>
              ),
            },
            {
              label: 'Non-veg menu only',
              prompt: buildRestoActionPrompt('nonveg', restaurant.name, provider),
              hint: 'Filter to non-vegetarian dishes',
              icon: (
                <span className="w-3 h-3 border border-red-600 rounded-sm flex items-center justify-center">
                  <span className="w-1 h-1 rounded-full bg-red-600" />
                </span>
              ),
            },
          ]}
        />
      )}

      {itemPopover && (
        <QuickActionPopover
          anchor={itemPopover.anchor}
          onClose={() => setItemPopover(null)}
          title={itemPopover.item.name}
          subtitle={
            formatPriceINR(itemPopover.item.price)
              ? `${formatPriceINR(itemPopover.item.price)} · ${restaurant.name}`
              : restaurant.name
          }
          disabled={disabled}
          onSelect={(a) => fire(a.prompt)}
          actions={[
            {
              label: 'Add to cart',
              prompt: buildItemActionPrompt('add', itemPopover.item, restaurant.name, provider),
              hint: `I'll add one to your ${theme.label} cart`,
              icon: <Plus size={14} strokeWidth={2.5} />,
              tone: 'primary',
            },
            {
              label: 'See details',
              prompt: buildItemActionPrompt('details', itemPopover.item, restaurant.name, provider),
              hint: 'Price, description and add-ons',
              icon: <Eye size={14} />,
            },
          ]}
        />
      )}
    </div>
  );
};

const RestaurantCardGrid: React.FC<{
  attachment: Attachment;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ attachment, onQuickPrompt, disabled }) => {
  const restaurants = Array.isArray(attachment.restaurants) ? attachment.restaurants : [];
  if (restaurants.length === 0) return null;
  const theme = themeFor(attachment.provider);
  const keyword = attachment.keyword ? String(attachment.keyword).trim() : '';
  const heading = keyword ? `Restaurants — ${keyword}` : 'Restaurants';

  return (
    <div className="mt-1 w-full max-w-full">
      <div className="flex items-center gap-1.5 mb-2 px-1">
        <TrendingUp size={12} style={{ color: theme.brandHex }} />
        {attachment.provider && (
          <span
            className="text-white font-bold text-[9px] tracking-wider px-1.5 py-0.5 rounded uppercase shrink-0"
            style={{ backgroundColor: theme.brandHex }}
          >
            {theme.label}
          </span>
        )}
        <div className="text-xs font-bold uppercase tracking-wider text-slate-500">
          {heading} ({restaurants.length})
        </div>
        {onQuickPrompt && (
          <div className="ml-auto text-[10px] text-slate-400 italic hidden sm:block">
            Tap a card or an item for quick actions
          </div>
        )}
      </div>
      {/* 4-column grid on desktop so 8 results land cleanly as 2 rows of
          4. Narrower viewports step down to 3 / 2 cols to avoid cramping;
          single-column fallback is intentionally skipped — restaurant
          cards work fine at half-width even on phones, and a single
          tall column buries the second row behind a long scroll. */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 px-1">
        {restaurants.map((r, idx) => (
          <RestaurantCard
            key={r.res_id || `${r.name}-${idx}`}
            restaurant={r}
            provider={attachment.provider}
            onQuickPrompt={onQuickPrompt}
            disabled={disabled}
          />
        ))}
      </div>
    </div>
  );
};

// Live cart-receipt card. Rendered for `cart_card` attachments — either
// auto-emitted right after a successful `create_cart` (so the user sees
// what just landed in their cart, the restaurant, the total, and the
// ETA without re-asking) or on-demand when the response LLM signals
// `show_cart: true` for cart-status follow-up turns ("what's in my
// cart?", "kitna ho gaya?", "ready to checkout?"). Reads its data
// straight from `attachment.cart` — same shape echoed back as
// `session_cart` on the next request, so the snapshot is single source
// of truth and the card stays in lockstep across turns.
//
// Split into four small sub-components (`CartHeader`, `RestaurantDetail`,
// `CartItemList`, `PriceBreakdown`) for readability — none of them are
// exported because they only make sense in this composition.
type SessionCartItemAddon = NonNullable<SessionCart['items'][number]['add_ons']>[number];

const CartHeader: React.FC<{
  paymentMethodType?: string | null;
  theme: ProviderTheme;
}> = ({ paymentMethodType, theme }) => {
  const method = (paymentMethodType || '').trim().toUpperCase();
  return (
    <div
      className="px-5 py-3.5 border-b flex items-center justify-between"
      style={{
        background: `linear-gradient(to right, ${theme.brandHex}26, transparent)`,
        borderBottomColor: `${theme.brandHex}33`,
      }}
    >
      <div className="flex items-center gap-2.5">
        <div
          className="w-8 h-8 rounded-lg text-white flex items-center justify-center shadow-sm shrink-0"
          style={{ backgroundColor: theme.brandHex }}
        >
          <ShoppingBag size={15} />
        </div>
        <div className="min-w-0">
          <span
            className="text-[10px] uppercase font-mono font-black tracking-widest block leading-none"
            style={{ color: theme.brandHex }}
          >
            {theme.cartHeaderTitle}
          </span>
          <h4 className="font-extrabold text-sm text-slate-900 leading-none mt-1">
            Basket Summary
          </h4>
        </div>
      </div>
      {method && (
        <span className="flex items-center gap-1 text-[10px] bg-emerald-100 text-emerald-800 font-extrabold px-2 py-1 rounded shrink-0">
          <CheckCircle2 size={11} className="text-emerald-600" />
          {method} Ready
        </span>
      )}
    </div>
  );
};

const RestaurantDetail: React.FC<{
  name: string;
  rating?: number;
  distanceKm?: number | null;
  eta?: string | null;
}> = ({ name, rating, distanceKm, eta }) => {
  const distanceLabel =
    typeof distanceKm === 'number' && distanceKm > 0
      ? `${distanceKm.toFixed(2)} km`
      : 'Nearby';
  return (
    <div className="p-4 md:p-5 border-b border-slate-100 bg-gradient-to-b from-white to-slate-50/30">
      <div className="flex justify-between items-center gap-3">
        <div className="min-w-0">
          <h3 className="font-black text-slate-900 text-base tracking-tight truncate">
            {name || 'Restaurant'}
          </h3>
          <div className="flex items-center gap-2 mt-2 text-xs text-slate-500 font-bold font-mono flex-wrap">
            <span className="flex items-center gap-1 bg-slate-100 px-2 py-0.5 rounded text-[10px]">
              <MapPin size={11} className="text-slate-400 shrink-0" />
              {distanceLabel}
            </span>
            {eta && (
              <span className="flex items-center gap-1 bg-slate-100 px-2 py-0.5 rounded text-[10px]">
                <Clock size={11} className="text-slate-400 shrink-0" />
                {eta}
              </span>
            )}
          </div>
        </div>
        {typeof rating === 'number' && rating > 0 && (
          <div className="flex items-center gap-1 bg-emerald-600 text-white font-black text-xs px-2.5 py-1 rounded shadow-sm shrink-0 leading-none">
            <Star size={12} className="fill-white shrink-0" />
            <span>{rating.toFixed(1)}</span>
          </div>
        )}
      </div>
    </div>
  );
};

// Tiny veg/egg/non-veg square indicator. Three-way because our Zomato
// snapshots tag items with both `veg` and `egg` in `item_tags`, and
// rendering everything else as red is wrong for egg-bearing items.
const FoodTypeMark: React.FC<{ tags: string[] }> = ({ tags }) => {
  const isVeg = tags.includes('veg');
  const isEgg = tags.includes('egg');
  const border = isVeg
    ? 'border-emerald-600'
    : isEgg
      ? 'border-amber-500'
      : 'border-red-600';
  const dot = isVeg
    ? 'bg-emerald-600'
    : isEgg
      ? 'bg-amber-500'
      : 'bg-red-600';
  return (
    <span
      className={cn(
        'w-4 h-4 border p-[2px] mt-0.5 flex items-center justify-center rounded-sm shrink-0',
        border,
      )}
    >
      <span className={cn('w-2 h-2 rounded-full', dot)} />
    </span>
  );
};

const CartItemList: React.FC<{
  items: SessionCart['items'];
  theme: ProviderTheme;
}> = ({ items, theme }) => {
  return (
    <div className="p-4 md:p-5 space-y-4">
      <div className="text-[10px] font-extrabold text-slate-400 uppercase tracking-widest flex items-center justify-between">
        <span>Cart Items</span>
        <span>
          {items.length} {items.length === 1 ? 'dish' : 'dishes'}
        </span>
      </div>

      {items.map((line, idx) => {
        const tags = Array.isArray(line.item_tags) ? line.item_tags : [];
        const price = typeof line.price === 'number' ? line.price : null;
        const discounted =
          typeof line.discounted_price === 'number' ? line.discounted_price : null;
        const payable = discounted ?? price;
        const showStrikethrough =
          discounted !== null && price !== null && discounted < price;
        const quantity =
          typeof line.quantity === 'number' && line.quantity > 0 ? line.quantity : 1;
        const addOns: SessionCartItemAddon[] = Array.isArray(line.add_ons)
          ? line.add_ons
          : [];

        return (
          <div
            key={line.item_id || line.variant_id || idx}
            className="space-y-2 border-b border-slate-100/60 pb-3 last:border-0 last:pb-0"
          >
            <div className="flex gap-4 justify-between items-start">
              <div className="flex-1 min-w-0 space-y-1">
                <div className="flex items-start gap-2">
                  <FoodTypeMark tags={tags} />
                  <div className="min-w-0">
                    <span className="font-extrabold text-slate-900 text-sm leading-snug">
                      {line.item_name}
                    </span>
                    <span className="text-xs font-black text-slate-400 ml-2">
                      × {quantity}
                    </span>
                  </div>
                </div>
                {line.description && (
                  <p className="text-[11px] text-slate-400 leading-relaxed ml-6 max-w-lg line-clamp-2">
                    {line.description}
                  </p>
                )}
              </div>

              {payable !== null && (
                <div className="text-right shrink-0">
                  <span className="font-mono text-slate-950 font-black text-sm">
                    ₹{payable}
                  </span>
                  {showStrikethrough && (
                    <p className="font-mono text-slate-400 line-through text-[10px]">
                      ₹{price}
                    </p>
                  )}
                </div>
              )}
            </div>

            {addOns.length > 0 && (
              <div className="ml-6 p-2.5 bg-slate-50 border border-slate-100 rounded-lg space-y-1">
                <div className="text-[9px] font-extrabold text-slate-400 uppercase tracking-widest flex items-center gap-1.5 leading-none mb-1">
                  <Layers size={11} style={{ color: theme.brandHex }} />
                  <span>Toppings &amp; Sides included</span>
                </div>
                {addOns.map((ao, adIdx) => (
                  <div
                    key={ao.id || `${ao.name}-${adIdx}`}
                    className="flex justify-between items-center text-[11px] font-bold text-slate-600"
                  >
                    <span className="flex items-center gap-1 text-slate-700 min-w-0">
                      <ChevronRight size={11} className="text-slate-400 shrink-0" />
                      <span className="truncate">{ao.name}</span>
                    </span>
                    <span className="text-[10px] text-emerald-600 font-extrabold bg-emerald-50 px-1.5 py-0.5 rounded shrink-0">
                      +{ao.quantity ?? 1}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

const PriceBreakdown: React.FC<{
  itemTotal?: number;
  platformFee: number;
  deliveryFee: number;
  taxes: number;
  promoCode?: string | null;
  promoDiscount?: number | null;
  finalAmount?: number;
  // Optional "Order now" CTA wired to the parent's checkout dispatcher.
  // When supplied, renders a bold full-width button under the total
  // that fires the existing chat pipeline ("Checkout my <provider>
  // cart …") so the agent runs the provider's normal checkout flow —
  // no new backend endpoint or special handling, the button is just
  // sugar over the same intent the user could type by hand.
  onPlaceOrder?: () => void;
  placeOrderDisabled?: boolean;
  theme: ProviderTheme;
}> = ({
  itemTotal,
  platformFee,
  deliveryFee,
  taxes,
  promoCode,
  promoDiscount,
  finalAmount,
  onPlaceOrder,
  placeOrderDisabled,
  theme,
}) => {
  const hasPromo = typeof promoDiscount === 'number' && promoDiscount > 0;
  return (
    <div className="p-4 md:p-5 flex-1 flex flex-col justify-between gap-4">
      <div className="space-y-2 text-xs font-semibold text-slate-500">
        <div className="flex items-center gap-1 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest mb-1">
          <Receipt size={13} />
          <span>Receipt Summary</span>
        </div>

        {typeof itemTotal === 'number' && (
          <div className="flex justify-between">
            <span>Items Subtotal</span>
            <span className="font-mono text-slate-800 font-bold">₹{itemTotal}</span>
          </div>
        )}
        {platformFee > 0 && (
          <div className="flex justify-between">
            <span>Platform Packaging Fee</span>
            <span className="font-mono text-slate-800 font-bold">
              ₹{platformFee.toFixed(2)}
            </span>
          </div>
        )}
        {deliveryFee > 0 && (
          <div className="flex justify-between">
            <span>Delivery Partner Fee</span>
            <span className="font-mono text-slate-800 font-bold">
              ₹{deliveryFee.toFixed(2)}
            </span>
          </div>
        )}
        {taxes > 0 && (
          <div className="flex justify-between">
            <span>GST &amp; Restaurant Services</span>
            <span className="font-mono text-slate-800 font-bold">
              ₹{taxes.toFixed(2)}
            </span>
          </div>
        )}
        {hasPromo && (
          <div className="flex justify-between text-emerald-600 font-extrabold bg-emerald-50 p-1.5 rounded">
            <span>Promo {promoCode ? `(${promoCode})` : ''}</span>
            <span className="font-mono">- ₹{promoDiscount}</span>
          </div>
        )}
      </div>

      <div className="space-y-3">
        {typeof finalAmount === 'number' && (
          <div className="pt-3 border-t border-dashed border-slate-200">
            <div className="flex justify-between items-end gap-2">
              <div>
                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">
                  Total Payable
                </span>
                <span
                  className="text-xl font-black font-mono"
                  style={{ color: theme.brandHex }}
                >
                  ₹{finalAmount.toFixed(2)}
                </span>
              </div>
              {hasPromo && (
                <div className="text-right">
                  <span className="text-[10px] text-emerald-600 font-black flex items-center justify-end gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                    Verified Savings
                  </span>
                  <span className="text-[9px] text-slate-400 block mt-0.5 font-medium">
                    Automatic discount applied
                  </span>
                </div>
              )}
            </div>
          </div>
        )}

        {onPlaceOrder && (
          <button
            type="button"
            onClick={onPlaceOrder}
            disabled={placeOrderDisabled}
            title={`Place this ${theme.label} order now`}
            style={{ backgroundColor: theme.brandHex }}
            className={cn(
              'w-full flex items-center justify-center gap-2 text-white font-extrabold text-sm py-3 rounded-xl shadow-md transition-all duration-200 active:scale-[0.98] hover:brightness-95',
              placeOrderDisabled
                ? 'opacity-60 cursor-not-allowed pointer-events-none hover:brightness-100'
                : 'cursor-pointer',
            )}
          >
            <ShoppingBag size={15} strokeWidth={2.5} />
            <span>
              Order now
              {typeof finalAmount === 'number' && (
                <span className="font-mono font-black ml-1.5">
                  · ₹{finalAmount.toFixed(2)}
                </span>
              )}
            </span>
          </button>
        )}
      </div>
    </div>
  );
};

const CartCard: React.FC<{
  cart: SessionCart;
  provider?: CardProvider;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ cart, provider, onQuickPrompt, disabled }) => {
  if (!cart || !cart.cart_id || !Array.isArray(cart.items) || cart.items.length === 0) {
    return null;
  }

  const theme = themeFor(provider);

  // "Order now" CTA — synthesises a clear checkout intent and ships
  // it through the same /api/agent/query/ pipeline as if the user had
  // typed it. The agent's planner already knows how to run each
  // provider's checkout flow (Zomato: place_order + UPI QR; Swiggy:
  // place_food_order + cash-on-delivery), so we don't need any new
  // backend wiring for this button — only the prompt copy changes per
  // provider so the stickiness check pins the turn to the right MCP.
  const handlePlaceOrder = onQuickPrompt
    ? () => onQuickPrompt(theme.checkoutPrompt)
    : undefined;

  // Pull the two well-known base_charges Zomato emits, plus the
  // taxes array, and aggregate. Same logic we had before — kept here
  // (not in PriceBreakdown) so the modular sub-components stay
  // declarative and the shape adapter lives in one place.
  const baseCharges = cart.charge_breakdown?.base_charges ?? [];
  const cartTaxes = cart.charge_breakdown?.taxes ?? [];
  const platformFee = baseCharges
    .filter((c) => c.charge_type === 'CHARGE_ID_PLATFORM')
    .reduce((sum, c) => sum + (c.amount || 0), 0);
  const deliveryFee = baseCharges
    .filter((c) => c.charge_type === 'CHARGE_ID_DELIVERY_CHARGE')
    .reduce((sum, c) => sum + (c.amount || 0), 0);
  const taxesTotal = cartTaxes.reduce((sum, t) => sum + (t.tax_amount || 0), 0);

  const cleanedUserMessage = (cart.user_message || '')
    .replace('[AI Agent Instruction]', '')
    .trim();

  return (
    <div className="mt-3 w-full max-w-full md:max-w-4xl animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden transition-all duration-300">
        <CartHeader paymentMethodType={cart.payment_method_type} theme={theme} />

        {/* 2-column split on desktop, stacked on mobile. Items live on
            the left (~58%) so descriptions and add-ons have room to
            breathe; receipt sits on the right (~42%) where the eye
            naturally lands for a total. */}
        <div className="flex flex-col md:flex-row divide-y md:divide-y-0 md:divide-x divide-slate-100">
          <div className="w-full md:w-7/12">
            <RestaurantDetail
              name={cart.restaurant_name || 'Restaurant'}
              rating={
                typeof cart.restaurant_rating === 'number'
                  ? cart.restaurant_rating
                  : undefined
              }
              distanceKm={cart.distance_in_km}
              eta={cart.eta}
            />
            <CartItemList items={cart.items} theme={theme} />
            {cleanedUserMessage && (
              <div className="mx-4 md:mx-5 mb-4 md:mb-5 bg-slate-50 border border-slate-100 p-2.5 rounded-lg text-[11px] text-slate-500 font-medium flex gap-2 items-start">
                <Info size={13} style={{ color: theme.brandHex }} className="shrink-0 mt-0.5" />
                <span className="leading-relaxed">{cleanedUserMessage}</span>
              </div>
            )}
          </div>

          <div className="w-full md:w-5/12 bg-slate-50/40 flex flex-col">
            <PriceBreakdown
              itemTotal={
                typeof cart.item_total === 'number' ? cart.item_total : undefined
              }
              platformFee={platformFee}
              deliveryFee={deliveryFee}
              taxes={taxesTotal}
              promoCode={cart.promo_code}
              promoDiscount={cart.promo_discount_amount}
              finalAmount={
                typeof cart.final_amount === 'number' ? cart.final_amount : undefined
              }
              onPlaceOrder={handlePlaceOrder}
              placeOrderDisabled={disabled}
              theme={theme}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export const AttachmentList: React.FC<{
  attachments: Attachment[];
  onQuickPrompt?: (prompt: string) => void;
  quickPromptDisabled?: boolean;
}> = ({ attachments, onQuickPrompt, quickPromptDisabled }) => {
  // Cross-provider turns ("compare pizza on Zomato and Swiggy") get a unified
  // head-to-head strip above the per-provider carousels.
  const compareColumns = buildCompareColumns(
    attachments.filter((att): att is Attachment => !!att && typeof att === 'object'),
  );
  const cards = attachments
    .filter((att): att is Attachment => !!att && typeof att === 'object')
    .map((att, idx) => {
      const kind = String(att.kind || '').toLowerCase();
      if (kind === 'menu_cards') {
        return <MenuCardGrid key={`menu-${idx}`} attachment={att} />;
      }
      if (kind === 'restaurant_cards') {
        return (
          <RestaurantCardGrid
            key={`resto-${idx}`}
            attachment={att}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'cart_card' && att.cart) {
        return (
          <CartCard
            key={`cart-${att.cart.cart_id || idx}`}
            cart={att.cart}
            provider={att.provider}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      // District card kinds — each one carries its own list under a typed
      // field on the Attachment shape (dining_restaurants / movies /
      // theatres / shows / events / venues / artists / reservation).
      if (kind === 'dining_cards') {
        return (
          <DiningCardGrid
            key={`district-dining-${idx}`}
            attachment={att}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'movie_cards') {
        return (
          <MovieCardGrid
            key={`district-movies-${idx}`}
            attachment={att}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'theatre_cards') {
        return (
          <TheatreCardGrid
            key={`district-theatres-${idx}`}
            attachment={att}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'show_cards') {
        return <ShowCardGrid key={`district-shows-${idx}`} attachment={att} />;
      }
      if (kind === 'event_cards') {
        return (
          <EventCardGrid
            key={`district-events-${idx}`}
            attachment={att}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'venue_cards') {
        return (
          <VenueCardGrid
            key={`district-venues-${idx}`}
            attachment={att}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'artist_cards') {
        return (
          <ArtistCardGrid
            key={`district-artists-${idx}`}
            attachment={att}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'reservation_card' && att.reservation) {
        return (
          <ReservationCardComponent
            key={`district-reservation-${att.reservation.booking_id || idx}`}
            reservation={att.reservation}
          />
        );
      }
      // Zepto: product grid + cart receipt. See ./components/ZeptoCards.tsx
      // for the visual layout; both pipe quick-prompt strings back through
      // the same /api/agent/query/ chat round-trip used everywhere else.
      if (kind === 'zepto_product_cards') {
        return (
          <ZeptoProductCardGrid
            key={`zepto-products-${idx}`}
            attachment={att}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'zepto_cart_card' && att.zepto_cart) {
        return (
          <ZeptoCartCard
            key={`zepto-cart-${idx}`}
            cart={att.zepto_cart}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      // Instamart: same shape as Zepto cards, different theme + spinId key.
      if (kind === 'instamart_product_cards') {
        return (
          <InstamartProductCardGrid
            key={`instamart-products-${idx}`}
            attachment={att}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'instamart_cart_card' && att.instamart_cart) {
        return (
          <InstamartCartCard
            key={`instamart-cart-${idx}`}
            cart={att.instamart_cart}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      // Kite (Zerodha trading): instrument tiles, portfolio tables, open
      // orders, margins, and the safety-critical order ticket. See
      // ./components/KiteCards.tsx — every action routes through the same
      // /api/agent/query/ chat round-trip via onQuickPrompt.
      if (kind === 'kite_instrument_cards') {
        return (
          <KiteInstrumentGrid
            key={`kite-instruments-${idx}`}
            attachment={att}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'kite_holdings_card' && att.holdings) {
        return (
          <KiteHoldingsCard
            key={`kite-holdings-${idx}`}
            holdings={att.holdings}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'kite_positions_card' && att.positions) {
        return (
          <KitePositionsCard
            key={`kite-positions-${idx}`}
            positions={att.positions}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'kite_open_orders_card' && att.orders) {
        return (
          <KiteOpenOrdersCard
            key={`kite-orders-${idx}`}
            orders={att.orders}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'kite_margin_card' && att.margin) {
        return (
          <KiteMarginCard
            key={`kite-margin-${idx}`}
            margin={att.margin}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'kite_order_ticket_card' && att.order_ticket) {
        return (
          <KiteOrderTicketCard
            key={`kite-ticket-${idx}`}
            ticket={att.order_ticket}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'kite_gtt_card' && att.gtts?.length) {
        return <KiteGttCard key={`kite-gtt-${idx}`} gtts={att.gtts} />;
      }
      if (kind === 'kite_trades_card' && att.trades?.length) {
        return <KiteTradesCard key={`kite-trades-${idx}`} trades={att.trades} />;
      }
      if (kind === 'kite_mf_holdings_card' && att.mf_holdings?.length) {
        return <KiteMfHoldingsCard key={`kite-mf-${idx}`} mfHoldings={att.mf_holdings} />;
      }
      // Long-tail result tiles (any provider): order history, live tracking,
      // coupons, reservation slots, critic reviews, and the generic info tile.
      if (kind === 'order_cards' && att.past_orders?.length) {
        return (
          <OrderCardGrid
            key={`orders-${idx}`}
            orders={att.past_orders}
            provider={att.provider}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'tracking_card' && att.tracking) {
        return (
          <TrackingCardView key={`tracking-${idx}`} tracking={att.tracking} provider={att.provider} />
        );
      }
      if (kind === 'coupon_cards' && att.coupons?.length) {
        return (
          <CouponCardGrid
            key={`coupons-${idx}`}
            coupons={att.coupons}
            provider={att.provider}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'slot_cards' && att.slots?.length) {
        return (
          <SlotCardGrid
            key={`slots-${idx}`}
            att={att}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'review_cards' && att.reviews?.length) {
        return (
          <ReviewCardGrid key={`reviews-${idx}`} reviews={att.reviews} provider={att.provider} />
        );
      }
      if (kind === 'info_card' && att.info) {
        return <InfoCardView key={`info-${idx}`} info={att.info} provider={att.provider} />;
      }
      // Serp data tiles: flights / hotels / places / Amazon / YouTube.
      if (kind === 'flight_cards' && att.flight_options?.length) {
        return (
          <FlightCardGrid
            key={`flights-${idx}`}
            flights={att.flight_options}
            route={att.route}
            leg={att.leg}
            onQuickPrompt={onQuickPrompt}
            disabled={quickPromptDisabled}
          />
        );
      }
      if (kind === 'hotel_cards' && att.hotel_options?.length) {
        return (
          <HotelCardGrid key={`hotels-${idx}`} hotels={att.hotel_options} keyword={att.keyword} />
        );
      }
      if (kind === 'place_cards' && att.place_results?.length) {
        return (
          <PlaceCardGrid key={`places-${idx}`} places={att.place_results} keyword={att.keyword} />
        );
      }
      if (kind === 'shop_product_cards' && att.shop_products?.length) {
        return (
          <ShopProductCardGrid
            key={`shop-${idx}`}
            products={att.shop_products}
            keyword={att.keyword}
          />
        );
      }
      if (kind === 'video_cards' && att.videos?.length) {
        return <VideoCardGrid key={`videos-${idx}`} videos={att.videos} keyword={att.keyword} />;
      }
      const mime = String(att.mime_type || 'image/png');
      const b64 = typeof att.data_b64 === 'string' ? att.data_b64 : '';
      const caption = att.caption ? String(att.caption) : '';
      const upi = typeof att.upi_link === 'string' ? att.upi_link.trim() : '';
      const showUpi = upi.toLowerCase().startsWith('upi://');
      const hasImage = kind === 'image' && b64 && mime.startsWith('image/');
      const showOversizeFallback = !hasImage && att.oversize === true;

      if (!hasImage && !showOversizeFallback && !caption && !showUpi) return null;

      return (
        <div
          key={idx}
          className="flex flex-col items-start gap-2 p-3 bg-white/90 border border-slate-200/80 rounded-2xl max-w-[280px] shadow-sm"
        >
          {hasImage && (
            <img
              src={`data:${mime};base64,${b64}`}
              alt={String(att.alt || 'attachment')}
              className="w-56 h-56 block bg-white border border-slate-200 rounded-xl p-2"
              style={{ imageRendering: 'pixelated' }}
            />
          )}
          {showOversizeFallback && (
            <div className="text-xs text-slate-500">
              QR is too large to inline — please use the UPI link.
            </div>
          )}
          {caption && <div className="text-xs text-slate-500 leading-snug">{caption}</div>}
          {showUpi && (
            <a
              href={upi}
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-slate-900 hover:bg-slate-800 text-white text-xs font-bold transition-colors shadow-sm"
            >
              <ExternalLink size={12} />
              Open in UPI app{att.amount_inr ? ` ₹${att.amount_inr}` : ''}
            </a>
          )}
        </div>
      );
    })
    .filter(Boolean);

  if (cards.length === 0 && compareColumns.length === 0) return null;
  return (
    <div className="flex flex-col gap-3 mt-1">
      {compareColumns.length >= 2 && (
        <CompareStrip
          columns={compareColumns}
          onQuickPrompt={onQuickPrompt}
          disabled={quickPromptDisabled}
        />
      )}
      {cards}
    </div>
  );
};

const formatTimestamp = (): string => {
  // Backend doesn't ship a timestamp on Message; we just show the
  // user's local hour:minute when the bubble renders so the chat has
  // a subtle time anchor like the reference comp.
  const now = new Date();
  return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

export const ChatLog: React.FC<ChatLogProps> = ({
  messages,
  isThinking,
  sessionAddresses,
  onSelectAddress,
  searchQuery,
  onQuickPrompt,
  quickPromptDisabled,
}) => {
  const endRef = useRef<HTMLDivElement | null>(null);
  const [likedMessages, setLikedMessages] = useState<Record<number, 'up' | 'down' | null>>({});

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  // Locate the latest assistant message — that's the only one allowed to
  // surface the address-choice chips.
  let lastAssistantIdx = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'assistant') {
      lastAssistantIdx = i;
      break;
    }
  }

  const q = searchQuery.trim().toLowerCase();
  const visibleMessages = q
    ? messages.filter((m) => (m.content || '').toLowerCase().includes(q))
    : messages;

  const handleLike = (idx: number, action: 'up' | 'down') => {
    setLikedMessages((prev) => ({ ...prev, [idx]: prev[idx] === action ? null : action }));
  };

  return (
    <div className="flex-1 overflow-y-auto px-3 md:px-6 py-5 md:py-6 space-y-6 md:space-y-7 scroll-smooth pb-36 md:pb-40">
      {q && visibleMessages.length === 0 && (
        <div className="text-center text-xs text-slate-500 italic">
          No messages match “{searchQuery}”.
        </div>
      )}

      {visibleMessages.map((msg) => {
        const i = messages.indexOf(msg);
        const isUser = msg.role === 'user';
        const liked = likedMessages[i] ?? null;

        return (
          <div
            key={i}
            className={cn('flex flex-col space-y-1.5', isUser ? 'items-end' : 'items-start')}
          >
            <div className={cn('flex gap-2.5 md:gap-3 max-w-[92%] md:max-w-[85%]', isUser ? 'flex-row-reverse' : 'flex-row')}>
              {!isUser && (
                <div
                  className="w-8 h-8 rounded-full bg-slate-950 text-white flex items-center justify-center font-bold relative shrink-0 text-[10px] shadow"
                  title="Companion"
                >
                  C
                  <span className="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-emerald-500 border-2 border-white" />
                </div>
              )}

              <div className="flex flex-col gap-1 min-w-0">
                <div
                  className={cn(
                    'py-3 px-5 rounded-3xl text-sm leading-relaxed shadow-sm max-w-full',
                    isUser
                      ? 'bg-slate-950 text-white rounded-tr-none font-medium'
                      : 'bg-white/90 text-slate-900 border border-slate-200/70 rounded-tl-none font-normal backdrop-blur-sm',
                  )}
                >
                  <div
                    className={cn(
                      'prose max-w-none prose-p:leading-relaxed prose-p:my-1.5',
                      isUser
                        ? 'prose-invert prose-strong:text-white prose-a:text-amber-300 prose-code:text-amber-200'
                        : 'prose-slate prose-strong:text-slate-900 prose-a:text-blue-600 prose-code:text-rose-600 prose-pre:bg-slate-900 prose-pre:text-slate-100',
                    )}
                  >
                    <ReactMarkdown remarkPlugins={[remarkBreaks]}>
                      {msg.content}
                    </ReactMarkdown>
                  </div>
                </div>

                {msg.attachments && msg.attachments.length > 0 && (
                  <AttachmentList
                    attachments={msg.attachments}
                    onQuickPrompt={onQuickPrompt}
                    quickPromptDisabled={quickPromptDisabled}
                  />
                )}

                {msg.role === 'assistant'
                  && i === lastAssistantIdx
                  && !isThinking
                  && sessionAddresses.length > 0
                  && looksLikeAddressPrompt(msg.content) && (
                    <AddressChoices addresses={sessionAddresses} onSelect={onSelectAddress} />
                  )}

                {msg.meta && msg.meta.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-1">
                    {msg.meta.map((m, idx) => (
                      <span
                        key={idx}
                        className="text-[10px] font-bold uppercase tracking-wider px-2 py-1 bg-white/70 border border-slate-200/70 rounded-md text-slate-500"
                      >
                        {m}
                      </span>
                    ))}
                  </div>
                )}

                {msg.trace && msg.trace.length > 0 && (
                  <details className="mt-2 w-full">
                    <summary className="text-xs text-slate-400 cursor-pointer hover:text-slate-700 transition-colors font-medium">
                      View trace details
                    </summary>
                    <div className="mt-2 space-y-2 p-3 bg-white/70 rounded-xl border border-slate-200/70">
                      {msg.trace.map((t: any, idx: number) => (
                        <div key={idx} className="text-[11px] grid grid-cols-[80px_1fr] gap-2">
                          <span className="text-slate-700 font-semibold">
                            {t.provider || t.stage}:
                          </span>
                          <span className="text-slate-500 truncate">{t.detail}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            </div>

            <div
              className={cn(
                'text-[10px] text-slate-400/90 px-2.5 flex items-center gap-3 select-none',
                isUser ? 'justify-end mr-2' : 'justify-start ml-11',
              )}
            >
              <span>{formatTimestamp()}</span>

              {!isUser && (
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => handleLike(i, 'up')}
                    className={cn(
                      'p-1 rounded transition-all cursor-pointer hover:text-emerald-600 hover:bg-emerald-50',
                      liked === 'up' && 'text-emerald-600 bg-emerald-50/60',
                    )}
                    title="Rate helpful"
                  >
                    <ThumbsUp size={11} />
                  </button>
                  <button
                    onClick={() => handleLike(i, 'down')}
                    className={cn(
                      'p-1 rounded transition-all cursor-pointer hover:text-rose-600 hover:bg-rose-50',
                      liked === 'down' && 'text-rose-600 bg-rose-50/60',
                    )}
                    title="Rate unhelpful"
                  >
                    <ThumbsDown size={11} />
                  </button>
                </div>
              )}
            </div>
          </div>
        );
      })}

      {isThinking && (
        <div className="flex items-start gap-3 select-none">
          <div className="w-8 h-8 rounded-full bg-slate-950 text-white flex items-center justify-center font-bold text-[10px] shadow">
            C
          </div>
          <div className="bg-white/95 border border-slate-200/70 py-3 px-5 rounded-3xl rounded-tl-none font-medium text-slate-500 text-xs flex items-center gap-2 shadow-sm">
            <RefreshCw size={13} className="animate-spin text-slate-400" />
            <span>Thinking…</span>
          </div>
        </div>
      )}

      <div ref={endRef} />
    </div>
  );
};
