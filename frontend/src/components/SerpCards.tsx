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

export const FlightCardGrid: React.FC<{
  flights: FlightOption[];
  route?: string;
  leg?: string;
  outboundDate?: string;
  returnDate?: string;
  onQuickPrompt?: QuickPrompt;
  disabled?: boolean;
}> = ({ flights, route, leg, outboundDate, returnDate, onQuickPrompt, disabled }) => (
  <Shell
    icon={<Plane size={14} />}
    label={leg === 'return' ? `Return flights${route ? ` — ${route}` : ''}` : `Flights${route ? ` — ${route}` : ''}`}
    trailing={`${flights.length} option${flights.length === 1 ? '' : 's'}`}
  >
    <Scroller>
      {flights.map((f, idx) => {
        const first = f.legs?.[0];
        const last = f.legs?.[f.legs.length - 1];
        const gflights = `https://www.google.com/travel/flights?q=${encodeURIComponent(
          `flights from ${first?.from || ''} to ${last?.to || ''}${outboundDate ? ` on ${outboundDate}` : ''}${returnDate ? ` returning ${returnDate}` : ''}`,
        )}`;
        return (
          <div
            key={idx}
            role="link"
            tabIndex={0}
            onClick={() => window.open(gflights, '_blank', 'noopener')}
            onKeyDown={(e) => e.key === 'Enter' && window.open(gflights, '_blank', 'noopener')}
            className="w-64 shrink-0 cursor-pointer rounded-xl border border-slate-200/80 bg-white p-3 outline-none hover:shadow-md hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-slate-300 transition-all"
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
                onClick={(e) => {
                  e.stopPropagation();
                  onQuickPrompt(`Show return flights for the ₹${f.price} option`);
                }}
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

export const NewsCardGrid: React.FC<{ articles: NewsArticle[]; keyword?: string }> = ({
  articles,
  keyword,
}) => (
  <Shell icon={<Newspaper size={14} />} label="News" trailing={keyword}>
    <Scroller>
      {articles.map((a, idx) => (
        <a
          key={idx}
          href={a.link || undefined}
          target="_blank"
          rel="noreferrer"
          className="w-64 shrink-0 rounded-xl border border-slate-200/80 bg-white overflow-hidden outline-none hover:shadow-md hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-slate-300 transition-all"
        >
          {a.image ? <img src={a.image} alt="" className="h-24 w-full object-cover" /> : null}
          <div className="p-2.5">
            <p className="text-[11px] font-bold text-slate-800 leading-snug line-clamp-3">
              {a.title}
            </p>
            <p className="mt-1.5 text-[9.5px] text-slate-500">
              {[a.source, a.date].filter(Boolean).join(' · ')}
            </p>
          </div>
        </a>
      ))}
    </Scroller>
  </Shell>
);
