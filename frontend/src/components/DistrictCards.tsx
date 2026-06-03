// District-specific chat card components.
//
// District spans three discovery surfaces (dining table reservations,
// movies/shows/theatres, events/venues/artists) plus a single-booking
// reservation snapshot. Each surface has its own card kind so the
// frontend can pick the right layout — the backend stamps every district
// attachment with `provider: "district"`, so styling routes through
// `themeFor('district')` (purple `#7C3AED`).
//
// Like the Zomato / Swiggy cards in `ChatLog.tsx`, every card supports an
// optional `onQuickPrompt` hook so the user can tap an action (e.g.
// "Show me available slots") and have it routed through `handleSendMessage`
// as if they typed it. This keeps the agent's intent-routing logic the
// single source of truth — the cards never call MCP directly.

import React, { useState } from 'react';
import {
  Star,
  Clock,
  Film,
  MapPin,
  CalendarDays,
  TicketCheck,
  Sparkles,
  Music2,
  Users2,
  Building2,
  Mic2,
  Tag,
  ExternalLink,
  CheckCircle2,
  AlertCircle,
  Receipt,
  Eye,
  CalendarRange,
} from 'lucide-react';
import type {
  Attachment,
  ArtistCardEntry,
  DiningCardEntry,
  EventCardEntry,
  MovieCardEntry,
  ReservationCard,
  ShowCardEntry,
  TheatreCardEntry,
  VenueCardEntry,
} from '../types';
import { cn } from '../utils';
import { themeFor } from '../ChatLog';

const formatPriceINR = (value: number | undefined): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null;
  return `₹${Math.round(value)}`;
};

const formatDistanceKm = (value: number | undefined): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
  if (value < 1) return `${Math.round(value * 1000)} m`;
  return `${value.toFixed(1)} km`;
};

// ---------------------------------------------------------------------------
// Shared header for every card grid: brand pill + tool descriptor + count.
// Keeps every district card list visually anchored to the District brand
// without the Zomato/Swiggy red-overlay style (those cards lead with an
// image header; District cards are smaller / more text-forward).
// ---------------------------------------------------------------------------
const DistrictGridHeader: React.FC<{
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
}> = ({ icon, title, subtitle }) => {
  const theme = themeFor('district');
  return (
    <div className="flex items-center gap-2 px-1">
      <div
        className="flex items-center gap-1.5 px-2 py-0.5 rounded-md text-white text-[10px] font-bold tracking-wider uppercase"
        style={{ backgroundColor: theme.brandHex }}
      >
        {icon}
        {theme.label}
      </div>
      <div className="flex flex-col leading-tight">
        <span className="text-xs font-semibold text-slate-700">{title}</span>
        {subtitle && (
          <span className="text-[10px] text-slate-500">{subtitle}</span>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 1. Dining cards (table reservations)
// ---------------------------------------------------------------------------
const DiningCard: React.FC<{
  restaurant: DiningCardEntry;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ restaurant, onQuickPrompt, disabled }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const theme = themeFor('district');
  const showImage = !!restaurant.image_url && !imageBroken;
  const rating =
    typeof restaurant.rating === 'number' && restaurant.rating > 0
      ? restaurant.rating.toFixed(1)
      : null;
  const cuisines = (restaurant.cuisines || []).slice(0, 3).join(' · ');
  const bookable = restaurant.bookable_via_agent === true;
  const distance =
    typeof restaurant.distance === 'number'
      ? formatDistanceKm(restaurant.distance)
      : typeof restaurant.distance === 'string'
      ? restaurant.distance
      : null;
  const priceForTwo =
    typeof restaurant.price_for_two === 'number'
      ? formatPriceINR(restaurant.price_for_two)
      : typeof restaurant.price_for_two === 'string'
      ? restaurant.price_for_two
      : null;

  const fire = (prompt: string) => {
    if (!onQuickPrompt || disabled) return;
    onQuickPrompt(prompt);
  };

  return (
    <div className="snap-start shrink-0 w-64 flex flex-col bg-white/95 border border-slate-200/80 rounded-2xl overflow-hidden shadow-sm hover:shadow-md hover:border-slate-300 transition-all">
      <div className="relative w-full h-24 bg-slate-100 overflow-hidden">
        {showImage ? (
          <img
            src={restaurant.image_url}
            alt={restaurant.name}
            loading="lazy"
            onError={() => setImageBroken(true)}
            className="w-full h-full object-cover"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-slate-400 bg-gradient-to-br from-violet-50 to-fuchsia-50">
            <Sparkles size={26} strokeWidth={1.5} />
          </div>
        )}
        {bookable && (
          <div className="absolute top-1.5 right-1.5 flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-emerald-500/90 text-white text-[9px] font-bold tracking-wider uppercase shadow-sm">
            <CheckCircle2 size={9} />
            Bookable
          </div>
        )}
      </div>
      <div className="flex flex-col gap-1 p-2.5">
        <div className="flex items-start gap-2 justify-between">
          <span className="text-sm font-semibold text-slate-900 leading-snug line-clamp-2">
            {restaurant.name}
          </span>
          {rating && (
            <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md bg-amber-50 text-amber-700 text-[10px] font-bold border border-amber-100 shrink-0">
              <Star size={9} className="fill-amber-500 text-amber-500" />
              {rating}
            </span>
          )}
        </div>
        {cuisines && (
          <span className="text-[11px] text-slate-500 line-clamp-1">{cuisines}</span>
        )}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-slate-500">
          {restaurant.locality && (
            <span className="inline-flex items-center gap-0.5">
              <MapPin size={10} />
              {restaurant.locality}
            </span>
          )}
          {distance && <span>{distance}</span>}
          {priceForTwo && <span>{priceForTwo} for 2</span>}
        </div>
        {onQuickPrompt && (
          <div className="flex flex-wrap gap-1 mt-2">
            <button
              type="button"
              onClick={() => fire(`Show me available reservation slots at ${restaurant.name}.`)}
              disabled={disabled}
              className={cn(
                'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold text-white shadow-sm transition-colors',
                disabled
                  ? 'cursor-not-allowed opacity-60'
                  : 'hover:brightness-110 cursor-pointer',
              )}
              style={{ backgroundColor: theme.brandHex }}
            >
              <CalendarDays size={10} />
              {bookable ? 'Book table' : 'Show slots'}
            </button>
            <button
              type="button"
              onClick={() => fire(`Tell me more about ${restaurant.name} — timings, address, what people say.`)}
              disabled={disabled}
              className={cn(
                'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 transition-colors',
                disabled && 'cursor-not-allowed opacity-60',
              )}
            >
              <Eye size={10} />
              Details
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export const DiningCardGrid: React.FC<{
  attachment: Attachment;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ attachment, onQuickPrompt, disabled }) => {
  const cards = Array.isArray(attachment.dining_restaurants)
    ? attachment.dining_restaurants
    : [];
  if (cards.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 mt-1">
      <DistrictGridHeader
        icon={<Sparkles size={9} />}
        title="Dining restaurants"
        subtitle={`${cards.length} option${cards.length === 1 ? '' : 's'}`}
      />
      <div className="flex gap-2 overflow-x-auto snap-x snap-mandatory pb-1 no-scrollbar">
        {cards.map((r) => (
          <DiningCard
            key={r.res_id}
            restaurant={r}
            onQuickPrompt={onQuickPrompt}
            disabled={disabled}
          />
        ))}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 2. Movie cards
// ---------------------------------------------------------------------------
const MovieCard: React.FC<{
  movie: MovieCardEntry;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ movie, onQuickPrompt, disabled }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const theme = themeFor('district');
  const showPoster = !!movie.poster_url && !imageBroken;
  const rating =
    typeof movie.rating === 'number' && movie.rating > 0
      ? movie.rating.toFixed(1)
      : null;
  const languages = (movie.languages || []).slice(0, 2).join(', ');
  const genres = (movie.genres || []).slice(0, 2).join(' / ');
  const duration =
    typeof movie.duration_minutes === 'number' && movie.duration_minutes > 0
      ? `${movie.duration_minutes} min`
      : null;

  const fire = (prompt: string) => {
    if (!onQuickPrompt || disabled) return;
    onQuickPrompt(prompt);
  };

  return (
    <div className="snap-start shrink-0 w-44 flex flex-col bg-white/95 border border-slate-200/80 rounded-2xl overflow-hidden shadow-sm hover:shadow-md hover:border-slate-300 transition-all">
      <div className="relative w-full aspect-[2/3] bg-slate-900 overflow-hidden">
        {showPoster ? (
          <img
            src={movie.poster_url}
            alt={movie.name}
            loading="lazy"
            onError={() => setImageBroken(true)}
            className="w-full h-full object-cover"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-slate-500 bg-gradient-to-br from-violet-900 to-fuchsia-900">
            <Film size={32} strokeWidth={1.5} />
          </div>
        )}
        {rating && (
          <div className="absolute bottom-1.5 left-1.5 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md bg-white/95 backdrop-blur-sm text-[10px] font-bold text-slate-800 border border-slate-200/70">
            <Star size={9} className="fill-amber-400 text-amber-500" />
            {rating}
          </div>
        )}
        {movie.cbfc_certification && (
          <div className="absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded-md bg-slate-900/85 text-white text-[9px] font-bold tracking-wider uppercase">
            {movie.cbfc_certification}
          </div>
        )}
      </div>
      <div className="flex flex-col gap-1 p-2.5">
        <span className="text-sm font-semibold text-slate-900 leading-snug line-clamp-2">
          {movie.name}
        </span>
        <div className="flex flex-wrap gap-x-2 gap-y-0 text-[10px] text-slate-500">
          {languages && <span>{languages}</span>}
          {genres && <span>{genres}</span>}
          {duration && (
            <span className="inline-flex items-center gap-0.5">
              <Clock size={9} />
              {duration}
            </span>
          )}
        </div>
        {onQuickPrompt && (
          <div className="flex flex-wrap gap-1 mt-2">
            <button
              type="button"
              onClick={() => fire(`Show me today's showtimes for ${movie.name} near me.`)}
              disabled={disabled}
              className={cn(
                'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold text-white shadow-sm transition-colors',
                disabled
                  ? 'cursor-not-allowed opacity-60'
                  : 'hover:brightness-110 cursor-pointer',
              )}
              style={{ backgroundColor: theme.brandHex }}
            >
              <TicketCheck size={10} />
              Showtimes
            </button>
            <button
              type="button"
              onClick={() => fire(`Show me critic reviews for ${movie.name}.`)}
              disabled={disabled}
              className={cn(
                'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 transition-colors',
                disabled && 'cursor-not-allowed opacity-60',
              )}
            >
              <Star size={10} />
              Reviews
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export const MovieCardGrid: React.FC<{
  attachment: Attachment;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ attachment, onQuickPrompt, disabled }) => {
  const cards = Array.isArray(attachment.movies) ? attachment.movies : [];
  if (cards.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 mt-1">
      <DistrictGridHeader
        icon={<Film size={9} />}
        title="Movies near you"
        subtitle={`${cards.length} title${cards.length === 1 ? '' : 's'}`}
      />
      <div className="flex gap-2 overflow-x-auto snap-x snap-mandatory pb-1 no-scrollbar">
        {cards.map((m) => (
          <MovieCard
            key={m.movie_id}
            movie={m}
            onQuickPrompt={onQuickPrompt}
            disabled={disabled}
          />
        ))}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 3. Theatre cards
// ---------------------------------------------------------------------------
const TheatreCard: React.FC<{
  theatre: TheatreCardEntry;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ theatre, onQuickPrompt, disabled }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const theme = themeFor('district');
  const showImage = !!theatre.image_url && !imageBroken;
  const distance =
    typeof theatre.distance === 'number'
      ? formatDistanceKm(theatre.distance)
      : typeof theatre.distance === 'string'
      ? theatre.distance
      : null;
  const fire = (prompt: string) => {
    if (!onQuickPrompt || disabled) return;
    onQuickPrompt(prompt);
  };
  return (
    <div className="snap-start shrink-0 w-56 flex flex-col bg-white/95 border border-slate-200/80 rounded-2xl overflow-hidden shadow-sm hover:shadow-md hover:border-slate-300 transition-all">
      <div className="relative w-full h-20 bg-slate-100 overflow-hidden">
        {showImage ? (
          <img
            src={theatre.image_url}
            alt={theatre.name}
            loading="lazy"
            onError={() => setImageBroken(true)}
            className="w-full h-full object-cover"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-slate-400 bg-gradient-to-br from-violet-100 to-purple-100">
            <Building2 size={22} strokeWidth={1.5} />
          </div>
        )}
      </div>
      <div className="flex flex-col gap-1 p-2.5">
        <span className="text-sm font-semibold text-slate-900 leading-snug line-clamp-2">
          {theatre.name}
        </span>
        <div className="flex items-center gap-2 text-[10px] text-slate-500">
          {theatre.locality && (
            <span className="inline-flex items-center gap-0.5">
              <MapPin size={10} />
              {theatre.locality}
            </span>
          )}
          {distance && <span>{distance}</span>}
        </div>
        {onQuickPrompt && (
          <button
            type="button"
            onClick={() => fire(`Show me today's showtimes at ${theatre.name}.`)}
            disabled={disabled}
            className={cn(
              'inline-flex items-center justify-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold text-white shadow-sm transition-colors mt-1',
              disabled
                ? 'cursor-not-allowed opacity-60'
                : 'hover:brightness-110 cursor-pointer',
            )}
            style={{ backgroundColor: theme.brandHex }}
          >
            <TicketCheck size={10} />
            Showtimes here
          </button>
        )}
      </div>
    </div>
  );
};

export const TheatreCardGrid: React.FC<{
  attachment: Attachment;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ attachment, onQuickPrompt, disabled }) => {
  const cards = Array.isArray(attachment.theatres) ? attachment.theatres : [];
  if (cards.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 mt-1">
      <DistrictGridHeader
        icon={<Building2 size={9} />}
        title="Theatres nearby"
        subtitle={`${cards.length} venue${cards.length === 1 ? '' : 's'}`}
      />
      <div className="flex gap-2 overflow-x-auto snap-x snap-mandatory pb-1 no-scrollbar">
        {cards.map((t) => (
          <TheatreCard
            key={t.theatre_id}
            theatre={t}
            onQuickPrompt={onQuickPrompt}
            disabled={disabled}
          />
        ))}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 4. Show cards (showtimes — compact rows)
// ---------------------------------------------------------------------------
const ShowCard: React.FC<{ show: ShowCardEntry }> = ({ show }) => {
  const priceBand =
    typeof show.min_price === 'number' && show.min_price > 0
      ? typeof show.max_price === 'number' && show.max_price > show.min_price
        ? `${formatPriceINR(show.min_price)} – ${formatPriceINR(show.max_price)}`
        : formatPriceINR(show.min_price)
      : null;
  return (
    <div className="snap-start shrink-0 w-56 flex flex-col gap-1 p-2.5 bg-white/95 border border-slate-200/80 rounded-xl shadow-sm hover:shadow-md hover:border-slate-300 transition-all">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-slate-900 leading-tight">
          {show.show_start_time || show.show_date || 'Showtime'}
        </span>
        {show.screen_type && (
          <span className="px-1.5 py-0.5 rounded-md bg-violet-50 text-violet-700 text-[10px] font-bold border border-violet-100">
            {show.screen_type}
          </span>
        )}
      </div>
      {show.movie_name && (
        <span className="text-[11px] text-slate-600 line-clamp-1">{show.movie_name}</span>
      )}
      {show.theatre_name && (
        <span className="text-[10px] text-slate-500 inline-flex items-center gap-0.5 line-clamp-1">
          <Building2 size={10} />
          {show.theatre_name}
        </span>
      )}
      <div className="flex items-center justify-between text-[10px] text-slate-500 mt-1">
        {show.language && <span>{show.language}</span>}
        {priceBand && <span className="font-semibold text-slate-700">{priceBand}</span>}
      </div>
      {show.web_url && (
        <a
          href={show.web_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center justify-center gap-1 px-2 py-1 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 text-[10px] font-semibold transition-colors mt-1"
        >
          <ExternalLink size={10} />
          Book on District
        </a>
      )}
    </div>
  );
};

export const ShowCardGrid: React.FC<{ attachment: Attachment }> = ({ attachment }) => {
  const cards = Array.isArray(attachment.shows) ? attachment.shows : [];
  if (cards.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 mt-1">
      <DistrictGridHeader
        icon={<TicketCheck size={9} />}
        title="Showtimes"
        subtitle={`${cards.length} show${cards.length === 1 ? '' : 's'}`}
      />
      <div className="flex gap-2 overflow-x-auto snap-x snap-mandatory pb-1 no-scrollbar">
        {cards.map((s) => (
          <ShowCard key={s.show_id} show={s} />
        ))}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 5. Event cards
// ---------------------------------------------------------------------------
const EventCard: React.FC<{
  event: EventCardEntry;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ event, onQuickPrompt, disabled }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const theme = themeFor('district');
  const showImage = !!event.image_url && !imageBroken;
  const tags = (event.tags || []).slice(0, 3);
  const rating =
    typeof event.rating === 'number' && event.rating > 0
      ? event.rating.toFixed(1)
      : null;
  const fire = (prompt: string) => {
    if (!onQuickPrompt || disabled) return;
    onQuickPrompt(prompt);
  };
  return (
    <div className="snap-start shrink-0 w-64 flex flex-col bg-white/95 border border-slate-200/80 rounded-2xl overflow-hidden shadow-sm hover:shadow-md hover:border-slate-300 transition-all">
      <div className="relative w-full h-32 bg-slate-100 overflow-hidden">
        {showImage ? (
          <img
            src={event.image_url}
            alt={event.name}
            loading="lazy"
            onError={() => setImageBroken(true)}
            className="w-full h-full object-cover"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-slate-400 bg-gradient-to-br from-violet-100 to-fuchsia-100">
            <Music2 size={28} strokeWidth={1.5} />
          </div>
        )}
        {event.offer_string && (
          <div className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded-md bg-emerald-500/90 text-white text-[9px] font-bold tracking-wider uppercase shadow-sm">
            {event.offer_string}
          </div>
        )}
        {rating && (
          <div className="absolute bottom-1.5 right-1.5 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md bg-white/95 backdrop-blur-sm text-[10px] font-bold text-slate-800 border border-slate-200/70">
            <Star size={9} className="fill-amber-400 text-amber-500" />
            {rating}
          </div>
        )}
      </div>
      <div className="flex flex-col gap-1 p-2.5">
        <span className="text-sm font-semibold text-slate-900 leading-snug line-clamp-2">
          {event.name}
        </span>
        {event.date_string && (
          <span className="text-[11px] text-slate-600 inline-flex items-center gap-0.5">
            <CalendarDays size={11} />
            {event.date_string}
          </span>
        )}
        {(event.venue_name || event.locality) && (
          <span className="text-[10px] text-slate-500 inline-flex items-center gap-0.5 line-clamp-1">
            <MapPin size={10} />
            {[event.venue_name, event.locality].filter(Boolean).join(', ')}
          </span>
        )}
        {tags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-0.5">
            {tags.map((t) => (
              <span
                key={t}
                className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md bg-violet-50 text-violet-700 text-[9px] font-semibold border border-violet-100"
              >
                <Tag size={8} />
                {t}
              </span>
            ))}
          </div>
        )}
        <div className="flex items-center justify-between mt-1">
          <span className="text-[11px] font-semibold text-slate-700">
            {event.price_string || (event.min_price ? `${formatPriceINR(event.min_price)} onwards` : 'See on District')}
          </span>
          {onQuickPrompt && (
            <button
              type="button"
              onClick={() => fire(`Tell me more about ${event.name} — venue, full date, ticket prices.`)}
              disabled={disabled}
              className={cn(
                'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold text-white shadow-sm transition-colors',
                disabled
                  ? 'cursor-not-allowed opacity-60'
                  : 'hover:brightness-110 cursor-pointer',
              )}
              style={{ backgroundColor: theme.brandHex }}
            >
              <Eye size={10} />
              Details
            </button>
          )}
        </div>
        {event.web_url && (
          <a
            href={event.web_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center justify-center gap-1 px-2 py-1 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 text-[10px] font-semibold transition-colors mt-1"
          >
            <ExternalLink size={10} />
            Book on District
          </a>
        )}
      </div>
    </div>
  );
};

export const EventCardGrid: React.FC<{
  attachment: Attachment;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ attachment, onQuickPrompt, disabled }) => {
  const cards = Array.isArray(attachment.events) ? attachment.events : [];
  if (cards.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 mt-1">
      <DistrictGridHeader
        icon={<Music2 size={9} />}
        title="Events near you"
        subtitle={`${cards.length} event${cards.length === 1 ? '' : 's'}`}
      />
      <div className="flex gap-2 overflow-x-auto snap-x snap-mandatory pb-1 no-scrollbar">
        {cards.map((e) => (
          <EventCard
            key={e.event_id}
            event={e}
            onQuickPrompt={onQuickPrompt}
            disabled={disabled}
          />
        ))}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 6. Venue cards (event venues)
// ---------------------------------------------------------------------------
const VenueCard: React.FC<{
  venue: VenueCardEntry;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ venue, onQuickPrompt, disabled }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const theme = themeFor('district');
  const showImage = !!venue.image_url && !imageBroken;
  const distance = formatDistanceKm(venue.distance_km);
  const eventNames = (venue.event_names || []).slice(0, 2);
  const categories = (venue.categories || []).slice(0, 3);
  const fire = (prompt: string) => {
    if (!onQuickPrompt || disabled) return;
    onQuickPrompt(prompt);
  };
  return (
    <div className="snap-start shrink-0 w-64 flex flex-col bg-white/95 border border-slate-200/80 rounded-2xl overflow-hidden shadow-sm hover:shadow-md hover:border-slate-300 transition-all">
      <div className="relative w-full h-24 bg-slate-100 overflow-hidden">
        {showImage ? (
          <img
            src={venue.image_url}
            alt={venue.name}
            loading="lazy"
            onError={() => setImageBroken(true)}
            className="w-full h-full object-cover"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-slate-400 bg-gradient-to-br from-violet-100 to-indigo-100">
            <Building2 size={26} strokeWidth={1.5} />
          </div>
        )}
      </div>
      <div className="flex flex-col gap-1 p-2.5">
        <span className="text-sm font-semibold text-slate-900 leading-snug line-clamp-2">
          {venue.name}
        </span>
        <div className="flex items-center gap-2 text-[10px] text-slate-500">
          {(venue.locality || venue.city) && (
            <span className="inline-flex items-center gap-0.5">
              <MapPin size={10} />
              {[venue.locality, venue.city].filter(Boolean).join(', ')}
            </span>
          )}
          {distance && <span>{distance}</span>}
        </div>
        {categories.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-0.5">
            {categories.map((c) => (
              <span
                key={c}
                className="px-1.5 py-0.5 rounded-md bg-violet-50 text-violet-700 text-[9px] font-semibold border border-violet-100"
              >
                {c}
              </span>
            ))}
          </div>
        )}
        {eventNames.length > 0 && (
          <span className="text-[10px] text-slate-500 line-clamp-2 mt-0.5">
            Upcoming: {eventNames.join(' · ')}
          </span>
        )}
        {onQuickPrompt && (
          <button
            type="button"
            onClick={() => fire(`Show me upcoming events at ${venue.name}.`)}
            disabled={disabled}
            className={cn(
              'inline-flex items-center justify-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold text-white shadow-sm transition-colors mt-1',
              disabled
                ? 'cursor-not-allowed opacity-60'
                : 'hover:brightness-110 cursor-pointer',
            )}
            style={{ backgroundColor: theme.brandHex }}
          >
            <CalendarRange size={10} />
            Events here
          </button>
        )}
      </div>
    </div>
  );
};

export const VenueCardGrid: React.FC<{
  attachment: Attachment;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ attachment, onQuickPrompt, disabled }) => {
  const cards = Array.isArray(attachment.venues) ? attachment.venues : [];
  if (cards.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 mt-1">
      <DistrictGridHeader
        icon={<Building2 size={9} />}
        title="Event venues"
        subtitle={`${cards.length} venue${cards.length === 1 ? '' : 's'}`}
      />
      <div className="flex gap-2 overflow-x-auto snap-x snap-mandatory pb-1 no-scrollbar">
        {cards.map((v) => (
          <VenueCard
            key={v.venue_id}
            venue={v}
            onQuickPrompt={onQuickPrompt}
            disabled={disabled}
          />
        ))}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 7. Artist cards
// ---------------------------------------------------------------------------
const ArtistCard: React.FC<{
  artist: ArtistCardEntry;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ artist, onQuickPrompt, disabled }) => {
  const [imageBroken, setImageBroken] = useState(false);
  const theme = themeFor('district');
  const showImage = !!artist.image_url && !imageBroken;
  const genres = (artist.genres || []).slice(0, 3);
  const fire = (prompt: string) => {
    if (!onQuickPrompt || disabled) return;
    onQuickPrompt(prompt);
  };
  return (
    <div className="snap-start shrink-0 w-48 flex flex-col bg-white/95 border border-slate-200/80 rounded-2xl overflow-hidden shadow-sm hover:shadow-md hover:border-slate-300 transition-all">
      <div className="relative w-full aspect-square bg-slate-100 overflow-hidden">
        {showImage ? (
          <img
            src={artist.image_url}
            alt={artist.name}
            loading="lazy"
            onError={() => setImageBroken(true)}
            className="w-full h-full object-cover"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-slate-400 bg-gradient-to-br from-violet-100 to-pink-100">
            <Mic2 size={30} strokeWidth={1.5} />
          </div>
        )}
        {typeof artist.upcoming_event_count === 'number'
          && artist.upcoming_event_count > 0 && (
          <div className="absolute bottom-1.5 right-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-white/95 backdrop-blur-sm text-[10px] font-bold text-slate-800 border border-slate-200/70">
            <CalendarDays size={9} />
            {artist.upcoming_event_count} upcoming
          </div>
        )}
      </div>
      <div className="flex flex-col gap-1 p-2.5">
        <span className="text-sm font-semibold text-slate-900 leading-snug line-clamp-1">
          {artist.name}
        </span>
        {artist.artist_type && (
          <span className="text-[10px] text-slate-500 capitalize">{artist.artist_type}</span>
        )}
        {genres.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-0.5">
            {genres.map((g) => (
              <span
                key={g}
                className="px-1.5 py-0.5 rounded-md bg-violet-50 text-violet-700 text-[9px] font-semibold border border-violet-100"
              >
                {g}
              </span>
            ))}
          </div>
        )}
        {onQuickPrompt && (
          <button
            type="button"
            onClick={() => fire(`Show me upcoming events for ${artist.name}.`)}
            disabled={disabled}
            className={cn(
              'inline-flex items-center justify-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold text-white shadow-sm transition-colors mt-1',
              disabled
                ? 'cursor-not-allowed opacity-60'
                : 'hover:brightness-110 cursor-pointer',
            )}
            style={{ backgroundColor: theme.brandHex }}
          >
            <CalendarRange size={10} />
            Upcoming shows
          </button>
        )}
      </div>
    </div>
  );
};

export const ArtistCardGrid: React.FC<{
  attachment: Attachment;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ attachment, onQuickPrompt, disabled }) => {
  const cards = Array.isArray(attachment.artists) ? attachment.artists : [];
  if (cards.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 mt-1">
      <DistrictGridHeader
        icon={<Mic2 size={9} />}
        title="Artists with upcoming shows"
        subtitle={`${cards.length} artist${cards.length === 1 ? '' : 's'}`}
      />
      <div className="flex gap-2 overflow-x-auto snap-x snap-mandatory pb-1 no-scrollbar">
        {cards.map((a) => (
          <ArtistCard
            key={a.artist_id}
            artist={a}
            onQuickPrompt={onQuickPrompt}
            disabled={disabled}
          />
        ))}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// 8. Reservation card (single, full-width — like the food cart card)
// ---------------------------------------------------------------------------
const formatBookingDate = (raw: string | undefined): string | null => {
  if (!raw) return null;
  // District's API gives `YYYYMMDD` for reservation slots and ISO-ish for
  // booking_date echoes. Try to humanize either shape; fall through to the
  // raw string if it doesn't parse.
  const match = raw.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (match) {
    const [, y, m, d] = match;
    try {
      const dt = new Date(`${y}-${m}-${d}T00:00:00`);
      if (!Number.isNaN(dt.getTime())) {
        return dt.toLocaleDateString(undefined, {
          weekday: 'short',
          day: 'numeric',
          month: 'short',
          year: 'numeric',
        });
      }
    } catch {
      /* fall through */
    }
  }
  return raw;
};

export const ReservationCardComponent: React.FC<{
  reservation: ReservationCard;
}> = ({ reservation }) => {
  const theme = themeFor('district');
  const status = (reservation.status || '').toLowerCase();
  const isConfirmed =
    status.includes('confirm') || status === 'success' || status === 'booked';
  const isFailed =
    status.includes('fail') || status.includes('error') || status.includes('cancel');
  const dateLabel = formatBookingDate(reservation.booking_date);
  const amount = formatPriceINR(reservation.final_amount);

  return (
    <div className="w-full max-w-md flex flex-col bg-white/95 border border-violet-100/80 rounded-2xl overflow-hidden shadow-sm">
      <div
        className="flex items-center gap-2 px-3 py-2 text-white"
        style={{ backgroundColor: theme.brandHex }}
      >
        <Receipt size={14} />
        <span className="text-xs font-bold tracking-wider uppercase">
          {theme.cartHeaderTitle}
        </span>
        <span className="ml-auto text-[10px] opacity-90">
          {reservation.booking_id}
        </span>
      </div>
      <div className="flex flex-col gap-2 p-3">
        {reservation.restaurant_name && (
          <span className="text-base font-semibold text-slate-900 leading-snug">
            {reservation.restaurant_name}
          </span>
        )}
        <div className="grid grid-cols-2 gap-2 text-[12px] text-slate-700">
          {dateLabel && (
            <div className="flex items-center gap-1.5">
              <CalendarDays size={12} className="text-violet-600" />
              <span>{dateLabel}</span>
            </div>
          )}
          {reservation.slot_time && (
            <div className="flex items-center gap-1.5">
              <Clock size={12} className="text-violet-600" />
              <span>{reservation.slot_time}</span>
            </div>
          )}
          {typeof reservation.party_size === 'number' && (
            <div className="flex items-center gap-1.5">
              <Users2 size={12} className="text-violet-600" />
              <span>
                {reservation.party_size} guest
                {reservation.party_size === 1 ? '' : 's'}
              </span>
            </div>
          )}
          {amount && (
            <div className="flex items-center gap-1.5">
              <Tag size={12} className="text-violet-600" />
              <span className="font-semibold">{amount}</span>
            </div>
          )}
        </div>
        {reservation.message && (
          <p className="text-[12px] text-slate-600 leading-snug">{reservation.message}</p>
        )}
        <div className="flex items-center gap-2 mt-1">
          <span
            className={cn(
              'inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wider border',
              isFailed
                ? 'bg-red-50 text-red-700 border-red-100'
                : isConfirmed
                ? 'bg-emerald-50 text-emerald-700 border-emerald-100'
                : 'bg-amber-50 text-amber-700 border-amber-100',
            )}
          >
            {isFailed ? (
              <AlertCircle size={9} />
            ) : isConfirmed ? (
              <CheckCircle2 size={9} />
            ) : (
              <Clock size={9} />
            )}
            {reservation.status || 'Pending'}
          </span>
          {reservation.payment_status && (
            <span className="text-[10px] text-slate-500 uppercase tracking-wider">
              Payment: {reservation.payment_status}
            </span>
          )}
        </div>
        {(reservation.confirmation_url || reservation.cancellation_url) && (
          <div className="flex flex-wrap gap-2 mt-1">
            {reservation.confirmation_url && (
              <a
                href={reservation.confirmation_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-slate-900 hover:bg-slate-800 text-white text-[11px] font-semibold transition-colors"
              >
                <ExternalLink size={11} />
                View on District
              </a>
            )}
            {reservation.cancellation_url && (
              <a
                href={reservation.cancellation_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 text-[11px] font-semibold transition-colors"
              >
                Cancel
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
