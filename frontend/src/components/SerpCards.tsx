/**
 * Serp data tiles: flights, hotels, places, Amazon products, YouTube videos.
 * Same visual language as the rest of the card system (white surface,
 * rounded-2xl, indigo serp brand header, mono money, 10–11px meta); product /
 * video / hotel / place tiles link out, flight tiles quick-prompt the
 * round-trip follow-up ("return flights for the ₹6,228 option").
 */

import React from 'react';
import {
  Plane,
  BedDouble,
  MapPinned,
  ShoppingCart,
  PlayCircle,
  ArrowRight,
  Star,
} from 'lucide-react';
import { BrandHeader } from './ResultCards';
import type {
  FlightOption,
  HotelOption,
  PlaceResult,
  ShopProduct,
  VideoResult,
} from '../types';

type QuickPrompt = ((prompt: string) => void) | undefined;

const Scroller: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="flex gap-2.5 overflow-x-auto px-3 py-3 [scrollbar-width:thin] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-thumb]:bg-slate-300">
    {children}
  </div>
);

const Shell: React.FC<{
  icon: React.ReactNode;
  label: string;
  trailing?: React.ReactNode;
  children: React.ReactNode;
}> = ({ icon, label, trailing, children }) => (
  <div className="mt-3 w-full max-w-full animate-fade-in">
    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      <BrandHeader provider="serp" icon={icon} label={label} trailing={trailing} />
      {children}
    </div>
  </div>
);

const RatingChip: React.FC<{ rating?: number; reviews?: number }> = ({ rating, reviews }) =>
  rating ? (
    <span className="inline-flex items-center gap-0.5 text-[10px] font-bold text-slate-700">
      <Star size={9} className="fill-amber-400 text-amber-400" />
      {rating}
      {reviews ? <span className="font-medium text-slate-400">({reviews})</span> : null}
    </span>
  ) : null;

const durationText = (minutes?: number): string | null => {
  if (!minutes || !Number.isFinite(minutes)) return null;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h}h ${m ? `${m}m` : ''}`.trim() : `${m}m`;
};

export const FlightCardGrid: React.FC<{
  flights: FlightOption[];
  route?: string;
  leg?: string;
  onQuickPrompt?: QuickPrompt;
  disabled?: boolean;
}> = ({ flights, route, leg, onQuickPrompt, disabled }) => (
  <Shell
    icon={<Plane size={14} />}
    label={leg === 'return' ? `Return flights${route ? ` — ${route}` : ''}` : `Flights${route ? ` — ${route}` : ''}`}
    trailing={`${flights.length} option${flights.length === 1 ? '' : 's'}`}
  >
    <Scroller>
      {flights.map((f, idx) => {
        const first = f.legs?.[0];
        const last = f.legs?.[f.legs.length - 1];
        return (
          <div
            key={idx}
            className="w-64 shrink-0 rounded-xl border border-slate-200/80 bg-white p-3 hover:shadow-md hover:-translate-y-0.5 transition-all"
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-1.5 min-w-0">
                {f.airline_logo ? (
                  <img src={f.airline_logo} alt="" className="h-4 w-4 shrink-0 object-contain" />
                ) : null}
                <span className="text-[11px] font-extrabold text-slate-800 truncate">
                  {(f.airlines || []).join(' + ') || 'Flight'}
                </span>
              </div>
              {typeof f.price === 'number' ? (
                <span className="font-mono text-[13px] font-extrabold text-slate-900 shrink-0">
                  ₹{f.price.toLocaleString('en-IN')}
                </span>
              ) : null}
            </div>
            <div className="mt-2 flex items-center gap-1.5 text-[12px] font-bold text-slate-800">
              <span>{first?.from}</span>
              <span className="text-[9.5px] font-semibold text-slate-400">
                {(first?.departs || '').split(' ')[1] || ''}
              </span>
              <ArrowRight size={11} className="text-slate-400" />
              <span>{last?.to}</span>
              <span className="text-[9.5px] font-semibold text-slate-400">
                {(last?.arrives || '').split(' ')[1] || ''}
              </span>
            </div>
            <p className="mt-1 text-[10px] text-slate-500 font-medium">
              {[durationText(f.total_duration_minutes), f.stops ? `${f.stops} stop${f.stops > 1 ? 's' : ''}` : 'non-stop']
                .filter(Boolean)
                .join(' · ')}
              {f.legs && f.legs.length > 1
                ? ` · via ${f.legs.slice(0, -1).map((l) => l.to).join(', ')}`
                : ''}
            </p>
            {onQuickPrompt && f.departure_token && typeof f.price === 'number' ? (
              <button
                type="button"
                disabled={disabled}
                onClick={() => onQuickPrompt(`Show return flights for the ₹${f.price} option`)}
                className="mt-2 w-full rounded-full border border-slate-200 py-1 text-[10px] font-bold text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition-colors"
              >
                Return options
              </button>
            ) : null}
          </div>
        );
      })}
    </Scroller>
  </Shell>
);

export const HotelCardGrid: React.FC<{ hotels: HotelOption[]; keyword?: string }> = ({
  hotels,
  keyword,
}) => (
  <Shell icon={<BedDouble size={14} />} label="Hotels" trailing={keyword}>
    <Scroller>
      {hotels.map((h, idx) => (
        <a
          key={idx}
          href={h.link || undefined}
          target="_blank"
          rel="noreferrer"
          className="w-56 shrink-0 rounded-xl border border-slate-200/80 bg-white overflow-hidden hover:shadow-md hover:-translate-y-0.5 transition-all"
        >
          {h.image ? (
            <img src={h.image} alt="" className="h-24 w-full object-cover" />
          ) : null}
          <div className="p-2.5">
            <p className="text-[11.5px] font-extrabold text-slate-900 leading-tight line-clamp-2">
              {h.name}
            </p>
            <div className="mt-1 flex items-center justify-between">
              <RatingChip rating={h.rating} reviews={h.reviews} />
              {h.rate_per_night ? (
                <span className="font-mono text-[12px] font-extrabold text-slate-900">
                  {h.rate_per_night}
                  <span className="text-[9px] font-semibold text-slate-400">/night</span>
                </span>
              ) : null}
            </div>
            {h.amenities?.length ? (
              <p className="mt-1 text-[9.5px] text-slate-500 line-clamp-1">
                {h.amenities.slice(0, 3).join(' · ')}
              </p>
            ) : null}
          </div>
        </a>
      ))}
    </Scroller>
  </Shell>
);

export const PlaceCardGrid: React.FC<{ places: PlaceResult[]; keyword?: string }> = ({
  places,
  keyword,
}) => (
  <Shell icon={<MapPinned size={14} />} label="Places" trailing={keyword}>
    <Scroller>
      {places.map((p, idx) => (
        <a
          key={idx}
          href={p.website || undefined}
          target="_blank"
          rel="noreferrer"
          className="w-56 shrink-0 rounded-xl border border-slate-200/80 bg-white p-2.5 hover:shadow-md hover:-translate-y-0.5 transition-all"
        >
          <div className="flex items-start gap-2">
            {p.image ? (
              <img src={p.image} alt="" className="h-10 w-10 shrink-0 rounded-lg object-cover" />
            ) : null}
            <div className="min-w-0">
              <p className="text-[11.5px] font-extrabold text-slate-900 leading-tight line-clamp-1">
                {p.name}
              </p>
              <p className="text-[9.5px] text-slate-500">{p.category}</p>
            </div>
          </div>
          <div className="mt-1.5 flex items-center justify-between">
            <RatingChip rating={p.rating} reviews={p.reviews} />
            {p.open_state ? (
              <span className="text-[9px] font-bold text-emerald-700">{p.open_state}</span>
            ) : null}
          </div>
          {p.address ? (
            <p className="mt-1 text-[9.5px] text-slate-500 line-clamp-2">{p.address}</p>
          ) : null}
        </a>
      ))}
    </Scroller>
  </Shell>
);

export const ShopProductCardGrid: React.FC<{ products: ShopProduct[]; keyword?: string }> = ({
  products,
  keyword,
}) => (
  <Shell icon={<ShoppingCart size={14} />} label="Amazon" trailing={keyword}>
    <Scroller>
      {products.map((p, idx) => (
        <a
          key={p.asin || idx}
          href={p.link || undefined}
          target="_blank"
          rel="noreferrer"
          className="w-44 shrink-0 rounded-xl border border-slate-200/80 bg-white p-2.5 hover:shadow-md hover:-translate-y-0.5 transition-all"
        >
          {p.image ? (
            <img src={p.image} alt="" className="h-24 w-full object-contain" />
          ) : null}
          <p className="mt-1.5 text-[10.5px] font-bold text-slate-800 leading-snug line-clamp-2">
            {p.name}
          </p>
          <div className="mt-1 flex items-center justify-between">
            <span className="font-mono text-[12px] font-extrabold text-slate-900">
              {typeof p.price === 'number' ? `₹${p.price.toLocaleString('en-IN')}` : p.price}
            </span>
            <RatingChip rating={p.rating} />
          </div>
        </a>
      ))}
    </Scroller>
  </Shell>
);

export const VideoCardGrid: React.FC<{ videos: VideoResult[]; keyword?: string }> = ({
  videos,
  keyword,
}) => (
  <Shell icon={<PlayCircle size={14} />} label="YouTube" trailing={keyword}>
    <Scroller>
      {videos.map((v, idx) => (
        <a
          key={idx}
          href={v.link || undefined}
          target="_blank"
          rel="noreferrer"
          className="w-56 shrink-0 rounded-xl border border-slate-200/80 bg-white overflow-hidden hover:shadow-md hover:-translate-y-0.5 transition-all"
        >
          {v.image ? (
            <div className="relative">
              <img src={v.image} alt="" className="h-28 w-full object-cover" />
              {v.length ? (
                <span className="absolute bottom-1 right-1 rounded bg-black/75 px-1 py-0.5 font-mono text-[9px] font-bold text-white">
                  {v.length}
                </span>
              ) : null}
            </div>
          ) : null}
          <div className="p-2.5">
            <p className="text-[11px] font-bold text-slate-800 leading-snug line-clamp-2">
              {v.title}
            </p>
            <p className="mt-1 text-[9.5px] text-slate-500">
              {[v.channel, typeof v.views === 'number' ? `${Intl.NumberFormat('en', { notation: 'compact' }).format(v.views)} views` : null]
                .filter(Boolean)
                .join(' · ')}
            </p>
          </div>
        </a>
      ))}
    </Scroller>
  </Shell>
);
