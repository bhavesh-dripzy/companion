/**
 * Serp data tiles: flights, hotels, places, Amazon products, YouTube videos.
 * Same visual language as the rest of the card system (white surface,
 * rounded-2xl, indigo serp brand header, mono money, 10–11px meta); product /
 * video / hotel / place tiles link out, flight tiles quick-prompt the
 * round-trip follow-up ("return flights for the ₹6,228 option").
 */

import React from 'react';
import {
  Newspaper,
  Plane,
  BedDouble,
  MapPinned,
  ShoppingCart,
  PlayCircle,
  ArrowRight,
  Star,
  TrendingUp,
  TrendingDown,
  Bookmark,
  RefreshCw,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import { BrandHeader } from './ResultCards';
import type {
  CardProvider,
  FlightOption,
  MetricCardPayload,
  NewsArticle,
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

const FLIGHT_SORTS = ['Recommended', 'Price', 'Duration', 'Departure'] as const;
type FlightSort = (typeof FLIGHT_SORTS)[number];
type DepBucket = 'any' | 'morning' | 'afternoon' | 'evening' | 'custom';

// "2026-07-09 06:00" → "06:00"
const hhmm = (s?: string): string => {
  const part = (s || '').split(' ')[1] || s || '';
  return part.slice(0, 5);
};
// → fractional hour (10:30 → 10.5), or null if unparseable
const timeNum = (s?: string): number | null => {
  const [h, m] = hhmm(s).split(':').map(Number);
  return Number.isFinite(h) ? h + (Number.isFinite(m) ? m / 60 : 0) : null;
};
const airlineOf = (f: FlightOption): string => (f.airlines || [])[0] || f.legs?.[0]?.airline || '';

export const FlightCardGrid: React.FC<{
  flights: FlightOption[];
  route?: string;
  leg?: string;
  outboundDate?: string;
  returnDate?: string;
  departBefore?: string;
  departAfter?: string;
  onQuickPrompt?: QuickPrompt;
  disabled?: boolean;
}> = ({ flights, route, leg, outboundDate, returnDate, departBefore, departAfter, onQuickPrompt, disabled }) => {
  const [sort, setSort] = React.useState<FlightSort>('Recommended');
  const [showFilters, setShowFilters] = React.useState(false);
  const [nonstopOnly, setNonstopOnly] = React.useState(false);
  // Departure-time filter: pre-applied from the NL request ("before 10am") if provided.
  const [dep, setDep] = React.useState<DepBucket>(departBefore || departAfter ? 'custom' : 'any');
  const [airlines, setAirlines] = React.useState<Set<string>>(new Set());

  const allAirlines = React.useMemo(
    () => Array.from(new Set(flights.map(airlineOf).filter(Boolean))),
    [flights],
  );

  const matchesDep = (f: FlightOption): boolean => {
    const h = timeNum(f.legs?.[0]?.departs);
    if (h == null) return true;
    if (dep === 'morning') return h < 12;
    if (dep === 'afternoon') return h >= 12 && h < 18;
    if (dep === 'evening') return h >= 18;
    if (dep === 'custom') {
      const b = timeNum(departBefore);
      const a = timeNum(departAfter);
      return (b == null || h < b) && (a == null || h >= a);
    }
    return true;
  };
  const filtered = React.useMemo(
    () =>
      flights.filter(
        (f) =>
          (!nonstopOnly || (f.stops || 0) === 0) &&
          matchesDep(f) &&
          (airlines.size === 0 || airlines.has(airlineOf(f))),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [flights, nonstopOnly, dep, airlines],
  );
  const cheapest = Math.min(
    ...filtered.map((f) => (typeof f.price === 'number' ? f.price : Infinity)),
  );
  const sorted = React.useMemo(() => {
    const a = [...filtered];
    if (sort === 'Price') a.sort((x, y) => (x.price ?? 1e9) - (y.price ?? 1e9));
    else if (sort === 'Duration')
      a.sort((x, y) => (x.total_duration_minutes ?? 1e9) - (y.total_duration_minutes ?? 1e9));
    else if (sort === 'Departure')
      a.sort((x, y) => hhmm(x.legs?.[0]?.departs).localeCompare(hhmm(y.legs?.[0]?.departs)));
    return a;
  }, [filtered, sort]);

  const activeFilters =
    (nonstopOnly ? 1 : 0) + (dep !== 'any' ? 1 : 0) + (airlines.size > 0 ? 1 : 0);
  const clearFilters = () => {
    setNonstopOnly(false);
    setDep('any');
    setAirlines(new Set());
  };
  const toggleAirline = (a: string) =>
    setAirlines((prev) => {
      const n = new Set(prev);
      n.has(a) ? n.delete(a) : n.add(a);
      return n;
    });
  const customLabel = [
    departBefore ? `Before ${departBefore}` : '',
    departAfter ? `After ${departAfter}` : '',
  ]
    .filter(Boolean)
    .join(' · ');
  const chip = (active: boolean) =>
    `shrink-0 px-3 py-1 rounded-full text-[11px] font-semibold border transition-colors ${
      active
        ? 'bg-[#2b2f72] text-white border-[#2b2f72]'
        : 'bg-white text-slate-500 border-slate-200 hover:border-slate-300'
    }`;
  const gflightsFor = (f: FlightOption): string => {
    const first = f.legs?.[0];
    const last = f.legs?.[f.legs.length - 1];
    return `https://www.google.com/travel/flights?q=${encodeURIComponent(
      `flights from ${first?.from || ''} to ${last?.to || ''}${outboundDate ? ` on ${outboundDate}` : ''}${returnDate ? ` returning ${returnDate}` : ''}`,
    )}`;
  };

  return (
    <div className="w-full max-w-full md:max-w-2xl rounded-2xl border border-slate-200/80 bg-white shadow-sm overflow-hidden animate-fade-in">
      {/* Header: route + dates */}
      <div className="px-4 pt-3.5 pb-3 border-b border-slate-100">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="w-8 h-8 rounded-lg bg-[#2b2f72]/10 text-[#2b2f72] flex items-center justify-center shrink-0">
            <Plane size={16} strokeWidth={2.2} />
          </span>
          <div className="min-w-0">
            <p className="text-[14px] font-bold text-[#221b2e] truncate">
              {route || 'Flights'}
              {leg === 'return' ? ' · Return' : ''}
            </p>
            <p className="text-[11px] text-slate-400 truncate">
              {[outboundDate, returnDate].filter(Boolean).join('  –  ') ||
                `${flights.length} option${flights.length === 1 ? '' : 's'}`}
            </p>
          </div>
        </div>
        {/* Sort tabs + Filters toggle */}
        <div className="mt-2.5 flex items-center gap-1.5 overflow-x-auto no-scrollbar">
          {FLIGHT_SORTS.map((s) => (
            <button key={s} type="button" onClick={() => setSort(s)} className={chip(sort === s)}>
              {s}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setShowFilters((v) => !v)}
            className={`shrink-0 ml-auto inline-flex items-center gap-1 px-3 py-1 rounded-full text-[11px] font-semibold border transition-colors ${
              showFilters || activeFilters
                ? 'bg-[#2b2f72]/10 text-[#2b2f72] border-[#2b2f72]/30'
                : 'bg-white text-slate-500 border-slate-200'
            }`}
          >
            <SlidersHorizontal size={12} /> Filters
            {activeFilters ? (
              <span className="ml-0.5 bg-[#2b2f72] text-white rounded-full px-1.5 text-[9px]">
                {activeFilters}
              </span>
            ) : null}
          </button>
        </div>

        {/* Filter panel */}
        {showFilters ? (
          <div className="mt-2.5 space-y-2.5 rounded-xl border border-slate-200 bg-slate-50/50 p-3">
            <div>
              <p className="text-[10px] font-semibold text-slate-400 mb-1">Stops</p>
              <div className="flex gap-1.5">
                <button type="button" onClick={() => setNonstopOnly(false)} className={chip(!nonstopOnly)}>
                  Any
                </button>
                <button type="button" onClick={() => setNonstopOnly(true)} className={chip(nonstopOnly)}>
                  Non-stop
                </button>
              </div>
            </div>
            <div>
              <p className="text-[10px] font-semibold text-slate-400 mb-1">Departure</p>
              <div className="flex flex-wrap gap-1.5">
                {customLabel ? (
                  <button type="button" onClick={() => setDep('custom')} className={chip(dep === 'custom')}>
                    {customLabel}
                  </button>
                ) : null}
                {(
                  [
                    ['any', 'Any'],
                    ['morning', 'Before 12'],
                    ['afternoon', '12–6 PM'],
                    ['evening', 'After 6 PM'],
                  ] as [DepBucket, string][]
                ).map(([k, label]) => (
                  <button key={k} type="button" onClick={() => setDep(k)} className={chip(dep === k)}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {allAirlines.length > 1 ? (
              <div>
                <p className="text-[10px] font-semibold text-slate-400 mb-1">Airlines</p>
                <div className="flex flex-wrap gap-1.5">
                  {allAirlines.map((a) => (
                    <button key={a} type="button" onClick={() => toggleAirline(a)} className={chip(airlines.has(a))}>
                      {a}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            {activeFilters ? (
              <button
                type="button"
                onClick={clearFilters}
                className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-500 hover:text-slate-700"
              >
                <X size={12} /> Clear filters
              </button>
            ) : null}
          </div>
        ) : null}

        {activeFilters ? (
          <p className="mt-2 text-[10.5px] text-slate-400">
            Showing {sorted.length} of {flights.length} flights
          </p>
        ) : null}
      </div>

      {/* Flight rows (scroll internally so a deep pool stays a compact card) */}
      <div className="divide-y divide-slate-100 max-h-[440px] overflow-y-auto">
        {sorted.length === 0 ? (
          <div className="px-4 py-6 text-center">
            <p className="text-[12px] text-slate-500">No flights match these filters.</p>
            <button
              type="button"
              onClick={clearFilters}
              className="mt-1.5 text-[12px] font-semibold text-[#2b2f72]"
            >
              Clear filters
            </button>
          </div>
        ) : null}
        {sorted.map((f, idx) => {
          const first = f.legs?.[0];
          const last = f.legs?.[f.legs.length - 1];
          const isCheapest = typeof f.price === 'number' && f.price === cheapest;
          return (
            <div key={idx} className="px-4 py-3">
              <div className="flex items-center gap-3">
                {/* Airline */}
                <div className="flex flex-col items-center gap-1 w-11 shrink-0">
                  {f.airline_logo ? (
                    <img src={f.airline_logo} alt="" className="h-6 w-6 object-contain" />
                  ) : (
                    <Plane size={16} className="text-slate-400" />
                  )}
                  <span className="text-[8px] font-semibold text-slate-500 text-center leading-tight truncate w-full">
                    {(f.airlines || [])[0] || first?.airline || ''}
                  </span>
                </div>
                {/* Times + route line */}
                <div className="flex-1 min-w-0 flex items-center gap-2">
                  <div className="text-center shrink-0">
                    <p className="text-[14px] font-bold text-slate-900 leading-none">
                      {hhmm(first?.departs)}
                    </p>
                    <p className="text-[10px] text-slate-400 mt-0.5">{first?.from}</p>
                  </div>
                  <div className="flex-1 min-w-0 flex flex-col items-center px-1">
                    <span className="text-[9px] text-slate-400">
                      {durationText(f.total_duration_minutes)}
                    </span>
                    <div className="w-full flex items-center gap-1 my-0.5">
                      <span className="h-px flex-1 bg-slate-200" />
                      <span className="w-1 h-1 rounded-full bg-slate-300" />
                      <span className="h-px flex-1 bg-slate-200" />
                    </div>
                    <span className="text-[9px] font-semibold text-[#2b2f72]">
                      {f.stops ? `${f.stops} stop${f.stops > 1 ? 's' : ''}` : 'Non-stop'}
                    </span>
                  </div>
                  <div className="text-center shrink-0">
                    <p className="text-[14px] font-bold text-slate-900 leading-none">
                      {hhmm(last?.arrives)}
                    </p>
                    <p className="text-[10px] text-slate-400 mt-0.5">{last?.to}</p>
                  </div>
                </div>
                {/* Price */}
                <div className="text-right shrink-0">
                  {typeof f.price === 'number' ? (
                    <p className="text-[15px] font-extrabold text-slate-900 leading-none">
                      ₹{f.price.toLocaleString('en-IN')}
                    </p>
                  ) : null}
                  <p className="text-[9px] text-slate-400 mt-0.5">per person</p>
                  {isCheapest ? (
                    <span className="inline-block mt-1 text-[8.5px] font-bold text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded">
                      Cheapest
                    </span>
                  ) : null}
                </div>
              </div>
              {/* Actions */}
              <div className="mt-2 flex items-center gap-2">
                <a
                  href={gflightsFor(f)}
                  target="_blank"
                  rel="noreferrer"
                  className="flex-1 text-center inline-flex items-center justify-center gap-1 rounded-lg border border-[#2b2f72]/30 py-1.5 text-[11px] font-bold text-[#2b2f72] hover:bg-[#2b2f72]/5 transition-colors"
                >
                  View <ArrowRight size={11} />
                </a>
                {onQuickPrompt && f.departure_token && typeof f.price === 'number' ? (
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onQuickPrompt(`Show return flights for the ₹${f.price} option`)}
                    className="flex-1 rounded-lg border border-slate-200 py-1.5 text-[11px] font-bold text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition-colors"
                  >
                    Return options
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>

      <p className="px-4 py-2.5 border-t border-slate-100 text-center text-[10px] text-slate-400">
        Prices per person · tap <span className="font-semibold text-slate-500">View</span> to book on
        Google Flights
      </p>
    </div>
  );
};

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
          className="w-56 shrink-0 rounded-xl border border-slate-200/80 bg-white overflow-hidden outline-none hover:shadow-md hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-slate-300 transition-all"
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
      {places.map((p, idx) => {
        const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(
          [p.name, p.address].filter(Boolean).join(' '),
        )}`;
        return (
          <a
            key={idx}
            href={p.website || mapsUrl}
            target="_blank"
            rel="noreferrer"
            className="w-60 shrink-0 rounded-xl border border-slate-200/80 bg-white overflow-hidden outline-none hover:shadow-md hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-slate-300 transition-all"
          >
            {p.image ? (
              <div className="relative h-24 w-full bg-slate-100">
                <img src={p.image} alt="" className="h-full w-full object-cover" />
                {p.open_state ? (
                  <span className="absolute bottom-1.5 left-1.5 rounded-full bg-white/95 px-2 py-0.5 text-[8.5px] font-extrabold uppercase tracking-wide text-emerald-700 shadow-sm">
                    {p.open_state}
                  </span>
                ) : null}
              </div>
            ) : null}
            <div className="p-2.5">
              <p className="text-[12px] font-extrabold text-slate-900 leading-tight line-clamp-1">
                {p.name}
              </p>
              <div className="mt-0.5 flex items-center gap-1.5 text-[9.5px] text-slate-500">
                <span>{p.category}</span>
                {!p.image && p.open_state ? (
                  <span className="font-bold text-emerald-700">{p.open_state}</span>
                ) : null}
              </div>
              <div className="mt-1.5">
                <RatingChip rating={p.rating} reviews={p.reviews} />
              </div>
              {p.address ? (
                <p className="mt-1 text-[9.5px] leading-snug text-slate-500 line-clamp-2">{p.address}</p>
              ) : null}
            </div>
          </a>
        );
      })}
    </Scroller>
  </Shell>
);

export const ShopProductCardGrid: React.FC<{
  products: ShopProduct[];
  keyword?: string;
  label?: string;
}> = ({ products, keyword, label }) => (
  <Shell icon={<ShoppingCart size={14} />} label={label || 'Amazon'} trailing={keyword}>
    <Scroller>
      {products.map((p, idx) => (
        <a
          key={p.asin || idx}
          href={p.link || undefined}
          target="_blank"
          rel="noreferrer"
          className="w-44 shrink-0 rounded-xl border border-slate-200/80 bg-white p-2.5 outline-none hover:shadow-md hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-slate-300 transition-all"
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
          {p.source || p.delivery ? (
            <p className="mt-1 text-[9.5px] text-slate-500 line-clamp-1">
              {p.source ? <span className="font-bold text-slate-600">{p.source}</span> : null}
              {p.source && p.delivery ? ' · ' : ''}
              {p.delivery || ''}
            </p>
          ) : null}
        </a>
      ))}
    </Scroller>
  </Shell>
);

const Sparkline: React.FC<{ points: number[]; up: boolean }> = ({ points, up }) => {
  const W = 220;
  const H = 56;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = W / (points.length - 1 || 1);
  const coords = points.map((p, i) => `${(i * step).toFixed(1)},${(H - 6 - ((p - min) / span) * (H - 12)).toFixed(1)}`);
  const color = up ? '#059669' : '#e11d48';
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-14" preserveAspectRatio="none">
      <polygon
        points={`0,${H} ${coords.join(' ')} ${W},${H}`}
        fill={color}
        opacity="0.08"
      />
      <polyline
        points={coords.join(' ')}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
};

export const MetricCard: React.FC<{ metric: MetricCardPayload; provider?: CardProvider }> = ({
  metric,
  provider,
}) => {
  const up = String(metric.direction || '').toLowerCase() !== 'down';
  const pct = typeof metric.delta_percent === 'number' ? metric.delta_percent : null;
  const value =
    typeof metric.value === 'number' ? metric.value.toLocaleString('en-IN') : metric.value;
  const stats = Object.entries(metric.stats || {}).slice(0, 6);
  return (
    <div className="mt-3 w-full max-w-md animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <BrandHeader
          provider={provider || 'serp'}
          icon={up ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
          label={metric.title || 'Metric'}
        />
        <div className="px-4 pt-3">
          <div className="flex items-baseline gap-2.5">
            <span className="font-mono text-[22px] font-extrabold text-slate-900">{value}</span>
            {pct !== null ? (
              <span
                className={`inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[11px] font-extrabold ${
                  up ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-600'
                }`}
              >
                {up ? '▲' : '▼'} {Math.abs(pct).toFixed(2)}%
              </span>
            ) : null}
          </div>
          {metric.subtitle || metric.as_of ? (
            <p className="mt-0.5 text-[9.5px] font-medium text-slate-400">
              {[metric.subtitle, metric.as_of].filter(Boolean).join(' · ')}
            </p>
          ) : null}
        </div>
        {metric.series && metric.series.length > 1 ? (
          <div className="px-4 pt-2">
            <Sparkline points={metric.series} up={up} />
            {metric.series_label ? (
              <p className="text-right text-[8.5px] font-semibold uppercase tracking-wide text-slate-400">
                {metric.series_label}
              </p>
            ) : null}
          </div>
        ) : null}
        {stats.length ? (
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 px-4 pb-3.5 pt-2">
            {stats.map(([label, value]) => (
              <div key={label} className="flex items-center justify-between gap-2 text-[10px]">
                <span className="text-slate-500 truncate">{label}</span>
                <span className="font-mono font-bold text-slate-800 shrink-0">{value}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="pb-3" />
        )}
      </div>
    </div>
  );
};

const VideoTile: React.FC<{ video: VideoResult }> = ({ video }) => {
  const [playing, setPlaying] = React.useState(false);
  const canEmbed = Boolean(video.video_id);
  return (
    <div className="w-72 shrink-0 rounded-xl border border-slate-200/80 bg-white overflow-hidden hover:shadow-md transition-all">
      <div className="relative aspect-video bg-slate-900">
        {playing && canEmbed ? (
          <iframe
            className="absolute inset-0 h-full w-full"
            src={`https://www.youtube.com/embed/${video.video_id}?autoplay=1&rel=0`}
            title={video.title || 'YouTube video'}
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
          />
        ) : (
          <button
            type="button"
            onClick={() => (canEmbed ? setPlaying(true) : window.open(video.link, '_blank'))}
            className="group absolute inset-0 h-full w-full"
            aria-label={`Play ${video.title || 'video'}`}
          >
            {video.image ? (
              <img src={video.image} alt="" className="h-full w-full object-cover" />
            ) : null}
            <span className="absolute inset-0 flex items-center justify-center bg-black/20 group-hover:bg-black/35 transition-colors">
              <span className="flex h-10 w-14 items-center justify-center rounded-xl bg-[#FF0000]/90 shadow-lg group-hover:scale-110 transition-transform">
                <PlayCircle size={22} className="text-white" />
              </span>
            </span>
            {video.length ? (
              <span className="absolute bottom-1 right-1 rounded bg-black/75 px-1 py-0.5 font-mono text-[9px] font-bold text-white">
                {video.length}
              </span>
            ) : null}
          </button>
        )}
      </div>
      <div className="p-2.5">
        <p className="text-[11px] font-bold text-slate-800 leading-snug line-clamp-2">
          {video.title}
        </p>
        <p className="mt-1 text-[9.5px] text-slate-500">
          {[
            video.channel,
            typeof video.views === 'number'
              ? `${Intl.NumberFormat('en', { notation: 'compact' }).format(video.views)} views`
              : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
      </div>
    </div>
  );
};

export const VideoCardGrid: React.FC<{ videos: VideoResult[]; keyword?: string }> = ({
  videos,
  keyword,
}) => (
  <Shell icon={<PlayCircle size={14} />} label="YouTube" trailing={keyword}>
    <Scroller>
      {videos.map((v, idx) => (
        <VideoTile key={v.video_id || idx} video={v} />
      ))}
    </Scroller>
  </Shell>
);

// Relative "20m ago / 3h ago / 2d ago" from newsdata's "YYYY-MM-DD HH:MM:SS" (UTC) timestamp.
function timeAgo(raw?: string): string {
  if (!raw) return '';
  const t = Date.parse(raw.includes('T') ? raw : raw.replace(' ', 'T') + 'Z');
  if (Number.isNaN(t)) return raw;
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (mins < 60) return `${mins || 1}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export const NewsCardGrid: React.FC<{
  articles: NewsArticle[];
  keyword?: string;
  onQuickPrompt?: QuickPrompt;
  disabled?: boolean;
}> = ({ articles, keyword, onQuickPrompt, disabled }) => (
  <div className="w-full max-w-full md:max-w-2xl rounded-2xl border border-slate-200/80 bg-white shadow-sm overflow-hidden animate-fade-in">
    {/* Header */}
    <div className="flex items-center gap-2.5 px-4 pt-3.5 pb-2.5">
      <span className="w-7 h-7 rounded-lg bg-[#7B6FBE]/10 text-[#7B6FBE] flex items-center justify-center shrink-0">
        <Newspaper size={15} strokeWidth={2.2} />
      </span>
      <span className="text-[14px] font-bold text-[#221b2e]">Top Headlines</span>
      {keyword ? (
        <span className="ml-auto text-[11px] font-medium text-slate-400 capitalize truncate max-w-[40%]">
          {keyword}
        </span>
      ) : null}
    </div>

    {/* Article rows */}
    <div className="divide-y divide-slate-100">
      {articles.map((a, idx) => (
        <a
          key={idx}
          href={a.link || undefined}
          target="_blank"
          rel="noreferrer"
          className="flex gap-3 px-4 py-3 outline-none hover:bg-slate-50/80 focus-visible:bg-slate-50 transition-colors"
        >
          {a.image ? (
            <img
              src={a.image}
              alt=""
              loading="lazy"
              className="w-[88px] h-[72px] rounded-xl object-cover shrink-0 bg-slate-100"
              onError={(e) => ((e.currentTarget.style.visibility = 'hidden'))}
            />
          ) : (
            <div className="w-[88px] h-[72px] rounded-xl bg-slate-100 shrink-0 flex items-center justify-center text-slate-300">
              <Newspaper size={20} />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-bold text-[#1a1530] leading-snug line-clamp-2">
              {a.title}
            </p>
            {a.description ? (
              <p className="mt-0.5 text-[11.5px] text-slate-500 leading-snug line-clamp-2">
                {a.description}
              </p>
            ) : null}
            <div className="mt-1.5 flex items-center gap-1.5 text-[10.5px] text-slate-400 min-w-0">
              {a.icon ? (
                <img
                  src={a.icon}
                  alt=""
                  className="w-3.5 h-3.5 rounded-[3px] object-contain shrink-0"
                  onError={(e) => (e.currentTarget.style.display = 'none')}
                />
              ) : null}
              {a.source ? (
                <span className="font-semibold text-slate-500 truncate max-w-[55%]">{a.source}</span>
              ) : null}
              {a.date ? (
                <>
                  <span className="text-slate-300">•</span>
                  <span className="shrink-0">{timeAgo(a.date)}</span>
                </>
              ) : null}
            </div>
          </div>
          <Bookmark size={15} className="text-slate-300 shrink-0 mt-0.5" />
        </a>
      ))}
    </div>

    {/* Footer */}
    {onQuickPrompt ? (
      <button
        type="button"
        onClick={() => !disabled && onQuickPrompt('aur latest news dikhao')}
        disabled={disabled}
        className="w-full py-3 border-t border-slate-100 text-[12.5px] font-semibold text-[#7B6FBE] hover:bg-[#7B6FBE]/5 disabled:opacity-50 transition-colors flex items-center justify-center gap-1.5"
      >
        <RefreshCw size={13} strokeWidth={2.4} /> More latest news
      </button>
    ) : null}
  </div>
);
