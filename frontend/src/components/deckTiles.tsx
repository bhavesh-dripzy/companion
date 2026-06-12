/**
 * Adapters that flatten every multi-result card attachment into the generic
 * `DeckGroup`/`DeckTile` contract consumed by <SwipeDeck/>. One builder per
 * attachment kind; CTA prompt strings mirror the equivalent grid-card
 * buttons verbatim so backend intent routing behaves identically in both
 * views. Single-snapshot kinds (carts, tickets, tracking, metric, info)
 * intentionally have no builder — a deck of one is worse than the card.
 */

import React from 'react';
import {
  BedDouble,
  CalendarDays,
  Film,
  MapPin,
  MapPinned,
  Music2,
  Newspaper,
  Plane,
  PlayCircle,
  Receipt,
  ShoppingBasket,
  ShoppingCart,
  Tag,
  Ticket,
  TrendingUp,
  UtensilsCrossed,
} from 'lucide-react';
import type { Attachment, CardProvider } from '../types';
import type { DeckGroup, DeckSpec, DeckStat, DeckTile } from './SwipeDeck';

const inr = (v?: number | string | null): string | undefined => {
  if (typeof v === 'number' && Number.isFinite(v) && v > 0) {
    return `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
  }
  if (typeof v === 'string' && v.trim()) return v.trim();
  return undefined;
};

const stat = (label: string, value?: string | number | null, note?: string | null): DeckStat | null =>
  value !== undefined && value !== null && String(value).trim() !== ''
    ? { label, value: String(value), note: note || undefined }
    : null;

const spec = (label: string, value?: string | number | null): DeckSpec | null =>
  value !== undefined && value !== null && String(value).trim() !== ''
    ? { label, value: String(value) }
    : null;

const compact = <T,>(arr: (T | null | undefined)[]): T[] => arr.filter(Boolean) as T[];

const providerSuffix = (p?: CardProvider): string =>
  p === 'swiggy' ? ' on Swiggy' : p === 'zomato' ? ' on Zomato' : '';

const durationText = (minutes?: number): string | undefined => {
  if (!minutes || !Number.isFinite(minutes)) return undefined;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h}h ${m ? `${m}m` : ''}`.trim() : `${m}m`;
};

const group = (label: string, icon: React.ReactNode, tiles: DeckTile[]): DeckGroup | null =>
  tiles.length >= 2 ? { label, icon, tiles } : null;

const restaurantDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.restaurants) ? att.restaurants : [];
  const ps = providerSuffix(att.provider);
  const tiles = list.map((r, i): DeckTile => {
    const cuisines = Array.isArray(r.cuisines) ? r.cuisines.filter(Boolean) : [];
    const distance = typeof r.distance_km === 'number' ? `${r.distance_km} km` : undefined;
    const votes = r.votes !== undefined && r.votes !== null ? String(r.votes) : undefined;
    const costForTwo = (r.cost_for_two || '').trim();
    return {
      id: String(r.res_id || `${r.name}-${i}`),
      kind: 'Restaurant',
      provider: att.provider,
      title: r.name,
      image: r.res_image,
      icon: <UtensilsCrossed size={20} />,
      ribbon: r.res_offer || undefined,
      rating: typeof r.rating === 'number' && r.rating > 0 ? r.rating : undefined,
      sub: compact([cuisines.join(' · '), r.locality, distance && `${distance} away`]).join(' — ') || undefined,
      price: costForTwo ? costForTwo.replace(/\s*for two\s*$/i, '') : undefined,
      priceNote: costForTwo ? 'for two' : undefined,
      stats: compact([
        stat('Rating', typeof r.rating === 'number' && r.rating > 0 ? `${r.rating.toFixed(1)} ★` : undefined, votes && `${votes} votes`),
        stat('ETA', r.eta),
        stat(costForTwo ? 'Cost' : 'Distance', costForTwo || distance),
      ]),
      specs: compact([
        spec('Cuisines', cuisines.join(', ')),
        spec('Locality', r.locality),
        spec('Cost for two', costForTwo),
        spec('Distance', distance),
        spec('Offer', r.res_offer),
        spec('Votes', votes),
        spec('Serviceability', r.serviceability_status),
        ...(Array.isArray(r.items) ? r.items : [])
          .slice(0, 4)
          .map((it) => spec('Top pick', compact([it.name, inr(it.price ?? it.min_price)]).join(' — '))),
      ]),
      cta: { label: 'View menu', prompt: `Show me the menu of ${r.name}${ps}.` },
      cta2: { label: 'Veg only', prompt: `Show me only the veg items from ${r.name}${ps}.` },
    };
  });
  const keyword = att.keyword ? String(att.keyword).trim() : '';
  return group(keyword ? `Restaurants — ${keyword}` : 'Restaurants', <UtensilsCrossed size={12} />, tiles);
};

const menuDeck = (att: Attachment): DeckGroup | null => {
  const items = Array.isArray(att.items) ? att.items : [];
  const resto = (att.restaurant_name || '').trim();
  const restoSuffix = resto ? ` from ${resto}` : '';
  const ps = providerSuffix(att.provider);
  const vegLabel = { veg: 'VEG', non_veg: 'NON-VEG', egg: 'EGG' } as const;
  const tiles = items.map((it, i): DeckTile => {
    const price = inr(it.price);
    const disc = inr(it.discounted_price);
    const hasDiscount = !!disc && !!price && disc !== price;
    return {
      id: String(it.variant_id || it.item_id || `${it.name}-${i}`),
      kind: 'Dish',
      provider: att.provider,
      title: it.name,
      image: it.image_url,
      icon: <UtensilsCrossed size={20} />,
      ribbon: it.veg_kind ? vegLabel[it.veg_kind] : undefined,
      rating: typeof it.rating === 'number' && it.rating > 0 ? it.rating : undefined,
      price: hasDiscount ? disc : price,
      priceNote: hasDiscount ? `was ${price}` : undefined,
      sub: it.description || undefined,
      stats: compact([
        stat('Price', hasDiscount ? disc : price),
        stat('Rating', typeof it.rating === 'number' && it.rating > 0 ? `${it.rating.toFixed(1)} ★` : undefined, it.votes ? `${it.votes} votes` : undefined),
        stat('Category', it.category),
      ]),
      specs: compact([
        spec('Description', it.description),
        spec('Category', it.category),
        spec('Restaurant', resto),
        spec('Type', it.veg_kind ? vegLabel[it.veg_kind] : undefined),
      ]),
      cta: { label: 'Add to cart', prompt: `Add 1 × ${it.name}${restoSuffix} to my cart${ps}.` },
      cta2: {
        label: 'Details',
        prompt: `Tell me more about ${it.name}${restoSuffix}${ps} — what's in it, price, and any add-ons.`,
      },
    };
  });
  return group(resto ? `Menu — ${resto}` : 'Menu', <UtensilsCrossed size={12} />, tiles);
};

const diningDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.dining_restaurants) ? att.dining_restaurants : [];
  const tiles = list.map((r, i): DeckTile => {
    const cuisines = Array.isArray(r.cuisines) ? r.cuisines.filter(Boolean) : [];
    return {
      id: String(r.res_id || `${r.name}-${i}`),
      kind: 'Dining',
      provider: att.provider,
      title: r.name,
      image: r.image_url,
      icon: <UtensilsCrossed size={20} />,
      rating: typeof r.rating === 'number' && r.rating > 0 ? r.rating : undefined,
      sub: compact([cuisines.join(' · '), r.locality]).join(' — ') || undefined,
      price: inr(r.price_for_two),
      priceNote: r.price_for_two ? 'for two' : undefined,
      stats: compact([
        stat('Rating', typeof r.rating === 'number' && r.rating > 0 ? `${r.rating.toFixed(1)} ★` : undefined),
        stat('For two', inr(r.price_for_two)),
        stat('Distance', r.distance),
      ]),
      specs: compact([
        spec('Cuisines', cuisines.join(', ')),
        spec('Locality', r.locality),
        spec('Bookable here', r.bookable_via_agent ? 'Yes — via Companion' : undefined),
      ]),
      cta: { label: 'See slots', prompt: `Show me available reservation slots at ${r.name}.` },
      cta2: { label: 'Details', prompt: `Tell me more about ${r.name} — timings, address, what people say.` },
    };
  });
  return group('Tables — District dining', <UtensilsCrossed size={12} />, tiles);
};

const movieDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.movies) ? att.movies : [];
  const tiles = list.map((m, i): DeckTile => ({
    id: String(m.movie_id || `${m.name}-${i}`),
    kind: 'Movie',
    provider: att.provider,
    title: m.name,
    image: m.poster_url,
    icon: <Film size={20} />,
    rating: typeof m.rating === 'number' && m.rating > 0 ? m.rating : undefined,
    ribbon: m.cbfc_certification ? `CBFC ${m.cbfc_certification}` : undefined,
    sub: compact([
      Array.isArray(m.genres) ? m.genres.join(' · ') : undefined,
      Array.isArray(m.languages) ? m.languages.join(', ') : undefined,
    ]).join(' — ') || undefined,
    stats: compact([
      stat('Rating', typeof m.rating === 'number' && m.rating > 0 ? `${m.rating.toFixed(1)} ★` : undefined),
      stat('Runtime', durationText(m.duration_minutes)),
      stat('Release', m.release_date),
    ]),
    specs: compact([
      spec('Genres', Array.isArray(m.genres) ? m.genres.join(', ') : undefined),
      spec('Languages', Array.isArray(m.languages) ? m.languages.join(', ') : undefined),
      spec('Runtime', durationText(m.duration_minutes)),
      spec('Certification', m.cbfc_certification),
      spec('Releases', m.release_date),
    ]),
    cta: { label: 'Showtimes', prompt: `Show me today's showtimes for ${m.name} near me.` },
    cta2: { label: 'Reviews', prompt: `Show me critic reviews for ${m.name}.` },
  }));
  return group('Movies — District', <Film size={12} />, tiles);
};

const theatreDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.theatres) ? att.theatres : [];
  const tiles = list.map((t, i): DeckTile => ({
    id: String(t.theatre_id || `${t.name}-${i}`),
    kind: 'Theatre',
    provider: att.provider,
    title: t.name,
    image: t.image_url,
    icon: <Film size={20} />,
    sub: compact([t.locality, t.distance && `${t.distance} away`]).join(' — ') || undefined,
    stats: compact([stat('Locality', t.locality), stat('Distance', t.distance)]),
    specs: compact([spec('Locality', t.locality), spec('Distance', t.distance)]),
    cta: { label: 'Showtimes', prompt: `Show me today's showtimes at ${t.name}.` },
    cta2: t.web_url ? { label: 'On District', href: t.web_url } : undefined,
  }));
  return group('Theatres — District', <Film size={12} />, tiles);
};

const showDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.shows) ? att.shows : [];
  const tiles = list.map((s, i): DeckTile => {
    const priceRange =
      s.min_price && s.max_price && s.min_price !== s.max_price
        ? `${inr(s.min_price)}–${inr(s.max_price)}`
        : inr(s.min_price ?? s.max_price);
    const bookUrl = s.booking_url || s.web_url;
    return {
      id: String(s.show_id || i),
      kind: 'Show',
      provider: att.provider,
      title: s.movie_name || 'Show',
      icon: <Ticket size={20} />,
      sub: compact([s.theatre_name, s.show_date, s.show_start_time]).join(' · ') || undefined,
      price: priceRange,
      priceNote: priceRange ? 'tickets' : undefined,
      stats: compact([
        stat('Time', s.show_start_time, s.show_date),
        stat('Screen', s.screen_type, s.language),
        stat('Seats', s.seat_availability),
      ]),
      specs: compact([
        spec('Theatre', s.theatre_name),
        spec('Date', s.show_date),
        spec('Starts', s.show_start_time),
        spec('Screen', s.screen_type),
        spec('Language', s.language),
        spec('Availability', s.seat_availability),
      ]),
      cta: bookUrl
        ? { label: 'Book on District', href: bookUrl }
        : { label: 'Details', prompt: `Tell me more about ${s.movie_name || 'this show'} at ${s.theatre_name || 'the theatre'}.` },
    };
  });
  return group('Shows — District', <Ticket size={12} />, tiles);
};

const eventDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.events) ? att.events : [];
  const tiles = list.map((ev, i): DeckTile => ({
    id: String(ev.event_id || `${ev.name}-${i}`),
    kind: 'Event',
    provider: att.provider,
    title: ev.name,
    image: ev.image_url,
    icon: <CalendarDays size={20} />,
    ribbon: ev.offer_string || undefined,
    rating: typeof ev.rating === 'number' && ev.rating > 0 ? ev.rating : undefined,
    sub: compact([ev.venue_name, ev.locality || ev.city, ev.date_string]).join(' · ') || undefined,
    price: ev.price_string || inr(ev.min_price),
    priceNote: ev.min_price ? 'onwards' : undefined,
    stats: compact([
      stat('Date', ev.date_string),
      stat('From', ev.price_string || inr(ev.min_price)),
      stat('Distance', typeof ev.distance_km === 'number' ? `${ev.distance_km} km` : undefined),
    ]),
    specs: compact([
      spec('Venue', compact([ev.venue_name, ev.locality, ev.city]).join(', ')),
      spec('Date', ev.date_string),
      spec(
        'Type',
        compact([
          ev.is_dining_event && 'Dining event',
          ev.is_activity && 'Activity',
          ev.is_play && 'Play',
          ev.is_attraction && 'Attraction',
        ]).join(' · ') || undefined,
      ),
      spec('Tags', Array.isArray(ev.tags) ? ev.tags.join(', ') : undefined),
      spec('Offer', ev.offer_string),
      spec('Reviews', ev.review_count ? `${ev.review_count}` : undefined),
      spec('About', ev.description ? `${ev.description.slice(0, 180)}${ev.description.length > 180 ? '…' : ''}` : undefined),
    ]),
    cta: { label: 'Details', prompt: `Tell me more about ${ev.name} — venue, full date, ticket prices.` },
    cta2: ev.web_url ? { label: 'Book on District', href: ev.web_url } : undefined,
  }));
  return group('Events — District', <CalendarDays size={12} />, tiles);
};

const venueDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.venues) ? att.venues : [];
  const tiles = list.map((v, i): DeckTile => ({
    id: String(v.venue_id || `${v.name}-${i}`),
    kind: 'Venue',
    provider: att.provider,
    title: v.name,
    image: v.image_url,
    icon: <MapPin size={20} />,
    sub: compact([v.locality, v.city]).join(', ') || undefined,
    stats: compact([
      stat('Upcoming', Array.isArray(v.event_names) ? `${v.event_names.length} events` : undefined),
      stat('Distance', typeof v.distance_km === 'number' ? `${v.distance_km} km` : undefined),
      stat('Category', Array.isArray(v.categories) ? v.categories[0] : undefined),
    ]),
    specs: compact([
      spec('Location', compact([v.locality, v.city]).join(', ')),
      spec('Categories', Array.isArray(v.categories) ? v.categories.join(', ') : undefined),
      spec('Happening here', Array.isArray(v.event_names) ? v.event_names.slice(0, 4).join(' · ') : undefined),
    ]),
    cta: { label: 'Upcoming events', prompt: `Show me upcoming events at ${v.name}.` },
    cta2: v.web_url ? { label: 'On District', href: v.web_url } : undefined,
  }));
  return group('Venues — District', <MapPin size={12} />, tiles);
};

const artistDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.artists) ? att.artists : [];
  const tiles = list.map((a, i): DeckTile => ({
    id: String(a.artist_id || `${a.name}-${i}`),
    kind: 'Artist',
    provider: att.provider,
    title: a.name,
    image: a.image_url,
    icon: <Music2 size={20} />,
    sub: compact([a.artist_type, Array.isArray(a.genres) ? a.genres.join(' · ') : undefined]).join(' — ') || undefined,
    stats: compact([
      stat('Upcoming', a.upcoming_event_count ? `${a.upcoming_event_count} events` : undefined),
      stat('Type', a.artist_type),
      stat('Genre', Array.isArray(a.genres) ? a.genres[0] : undefined),
    ]),
    specs: compact([
      spec('Genres', Array.isArray(a.genres) ? a.genres.join(', ') : undefined),
      spec('About', a.description ? `${a.description.slice(0, 180)}${a.description.length > 180 ? '…' : ''}` : undefined),
    ]),
    cta: { label: 'Upcoming events', prompt: `Show me upcoming events for ${a.name}.` },
  }));
  return group('Artists — District', <Music2 size={12} />, tiles);
};

const zeptoDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.products) ? att.products : [];
  const tiles = list.map((p, i): DeckTile => {
    const price = inr(p.price_inr) || p.price_text || undefined;
    const mrp = inr(p.mrp_inr);
    const hasDiscount = !!mrp && !!price && mrp !== price;
    return {
      id: String(p.product_variant_id || `${p.name}-${i}`),
      kind: 'Grocery',
      provider: att.provider || 'zepto',
      title: p.name,
      image: p.image_url,
      icon: <ShoppingBasket size={20} />,
      ribbon: p.is_ad ? 'SPONSORED' : undefined,
      price,
      priceNote: hasDiscount ? `MRP ${mrp}` : undefined,
      sub: compact([p.pack_size, typeof p.available_quantity === 'number' ? `${p.available_quantity} in stock` : undefined]).join(' · ') || undefined,
      stats: compact([
        stat('Price', price, hasDiscount ? `MRP ${mrp}` : undefined),
        stat('Pack', p.pack_size),
        stat('Stock', typeof p.available_quantity === 'number' ? `${p.available_quantity}` : undefined),
      ]),
      specs: compact([
        spec('Pack size', p.pack_size),
        spec('MRP', mrp),
        spec('Availability', typeof p.available_quantity === 'number' ? `${p.available_quantity} units` : undefined),
        spec('Matched query', p.query),
      ]),
      cta: { label: 'Add to cart', prompt: `Add 1 ${p.name} to my Zepto cart.` },
    };
  });
  return group('Zepto — quick groceries', <ShoppingBasket size={12} />, tiles);
};

const instamartDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.instamart_products) ? att.instamart_products : [];
  const tiles = list.map((p, i): DeckTile => {
    const price = inr(p.price_inr);
    const mrp = inr(p.mrp_inr);
    const hasDiscount = !!mrp && !!price && mrp !== price;
    const headline = [p.brand?.trim(), p.name].filter(Boolean).join(' ');
    const tail = p.quantity_description?.trim() ? ` (${p.quantity_description.trim()})` : '';
    const outOfStock = p.in_stock === false;
    return {
      id: String(p.spin_id || `${p.name}-${i}`),
      kind: 'Grocery',
      provider: att.provider || 'instamart',
      title: p.name,
      image: p.image_url,
      icon: <ShoppingBasket size={20} />,
      ribbon: outOfStock ? 'OUT OF STOCK' : p.is_promoted ? 'SPONSORED' : undefined,
      price,
      priceNote: hasDiscount ? `MRP ${mrp}` : undefined,
      sub: compact([p.brand, p.quantity_description]).join(' · ') || undefined,
      stats: compact([
        stat('Price', price, hasDiscount ? `MRP ${mrp}` : undefined),
        stat('Pack', p.quantity_description),
        stat('Stock', outOfStock ? 'Out' : 'In stock'),
      ]),
      specs: compact([
        spec('Brand', p.brand),
        spec('Pack', p.quantity_description),
        spec('MRP', mrp),
        spec('Matched query', p.query),
      ]),
      cta: outOfStock ? undefined : { label: 'Add to cart', prompt: `Add 1 ${headline}${tail} to my Instamart cart.` },
    };
  });
  return group('Instamart — quick groceries', <ShoppingBasket size={12} />, tiles);
};

const kiteDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.instruments) ? att.instruments : [];
  const tiles = list.map((k, i): DeckTile => {
    const sym = (k.tradingsymbol || '').trim() || 'Instrument';
    const fetched = k.price_fetched_at
      ? new Date(k.price_fetched_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : undefined;
    return {
      id: String(k.instrument_token || `${sym}-${i}`),
      kind: 'Instrument',
      provider: 'kite',
      title: sym,
      icon: <TrendingUp size={20} />,
      sub: compact([k.name, k.exchange]).join(' · ') || undefined,
      price: inr(k.last_price),
      priceNote: fetched ? `as of ${fetched}` : k.last_price ? 'LTP' : undefined,
      stats: compact([
        stat('LTP', inr(k.last_price), fetched && `as of ${fetched}`),
        stat('Exchange', k.exchange),
        stat('Name', k.name),
      ]),
      specs: compact([
        spec('Symbol', sym),
        spec('Name', k.name),
        spec('Exchange', k.exchange),
        spec('Last price', inr(k.last_price)),
        spec('Quote time', fetched),
      ]),
      cta: { label: 'Show details', prompt: `Show details for ${sym}.` },
    };
  });
  return group('Instruments — Kite', <TrendingUp size={12} />, tiles);
};

const orderDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.past_orders) ? att.past_orders : [];
  const tiles = list.map((o, i): DeckTile => {
    const items = Array.isArray(o.items) ? o.items : [];
    return {
      id: String(o.order_id || i),
      kind: 'Order',
      provider: att.provider,
      title: o.place_name || `Order ${o.order_id}`,
      icon: <Receipt size={20} />,
      price: o.total_text || inr(o.total),
      sub: compact([o.status, o.date]).join(' · ') || undefined,
      stats: compact([stat('Status', o.status), stat('Date', o.date), stat('ETA', o.eta)]),
      specs: compact([
        spec('Order ID', o.order_id),
        spec('Status', o.status),
        spec('Date', o.date),
        ...items.slice(0, 5).map((it) => spec('Item', `${it.quantity && it.quantity > 1 ? `${it.quantity}× ` : ''}${it.name}`)),
      ]),
      cta: o.place_name ? { label: 'Reorder', prompt: `Reorder my last order from ${o.place_name}` } : undefined,
    };
  });
  return group('Past orders', <Receipt size={12} />, tiles);
};

const couponDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.coupons) ? att.coupons : [];
  const tiles = list.map((c, i): DeckTile => ({
    id: String(c.code || c.title || i),
    kind: 'Coupon',
    provider: att.provider,
    title: c.code || c.title || 'Coupon',
    icon: <Tag size={20} />,
    ribbon: c.savings_text || undefined,
    sub: compact([c.title !== c.code ? c.title : undefined, c.description]).join(' — ') || undefined,
    stats: compact([stat('Code', c.code), stat('Saves', c.savings_text)]),
    specs: compact([spec('Code', c.code), spec('Offer', c.title), spec('Details', c.description), spec('Savings', c.savings_text)]),
    cta: c.code ? { label: 'Apply coupon', prompt: `Apply coupon ${c.code} to my cart` } : undefined,
  }));
  return group('Coupons', <Tag size={12} />, tiles);
};

const flightDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.flight_options) ? att.flight_options : [];
  const tiles = list.map((f, i): DeckTile => {
    const first = f.legs?.[0];
    const last = f.legs?.[f.legs.length - 1];
    const airlines = Array.isArray(f.airlines) ? f.airlines.join(', ') : undefined;
    const stops = typeof f.stops === 'number' ? (f.stops === 0 ? 'Non-stop' : `${f.stops} stop${f.stops > 1 ? 's' : ''}`) : undefined;
    const gflights = `https://www.google.com/travel/flights?q=${encodeURIComponent(
      `flights from ${first?.from || ''} to ${last?.to || ''}${att.outbound_date ? ` on ${att.outbound_date}` : ''}${att.return_date ? ` returning ${att.return_date}` : ''}`,
    )}`;
    return {
      id: `${f.booking_token || f.departure_token || 'flight'}-${i}`,
      kind: 'Flight',
      provider: att.provider || 'serp',
      title: `${first?.from || '—'} → ${last?.to || '—'}`,
      icon: <Plane size={20} />,
      price: inr(f.price),
      priceNote:
        f.price && att.lowest_price && f.price === att.lowest_price
          ? 'lowest today'
          : att.leg === 'return'
            ? 'return leg'
            : 'one way',
      ribbon: stops,
      sub: compact([airlines, first?.departs && last?.arrives ? `${first.departs} → ${last.arrives}` : undefined]).join(' · ') || undefined,
      stats: compact([
        stat('Departs', first?.departs, att.outbound_date),
        stat('Duration', durationText(f.total_duration_minutes)),
        stat('Stops', stops),
      ]),
      specs: compact([
        ...(f.legs || []).map((leg) =>
          spec(
            compact([leg.airline, leg.flight_number]).join(' ') || 'Leg',
            `${leg.from || '—'} ${leg.departs || ''} → ${leg.to || '—'} ${leg.arrives || ''}`.trim(),
          ),
        ),
        spec('Airlines', airlines),
        spec('Total duration', durationText(f.total_duration_minutes)),
        spec('Date', att.outbound_date),
        spec('Return', att.return_date),
        spec('Lowest today', inr(att.lowest_price)),
      ]),
      cta: { label: 'Open in Google Flights', href: gflights },
      cta2:
        f.price && att.leg !== 'return'
          ? { label: 'Return options', prompt: `Show return flights for the ₹${f.price} option` }
          : undefined,
    };
  });
  return group(
    att.leg === 'return' ? `Return flights${att.route ? ` — ${att.route}` : ''}` : `Flights${att.route ? ` — ${att.route}` : ''}`,
    <Plane size={12} />,
    tiles,
  );
};

const hotelDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.hotel_options) ? att.hotel_options : [];
  const tiles = list.map((h, i): DeckTile => ({
    id: `${h.name || 'hotel'}-${i}`,
    kind: 'Hotel',
    provider: att.provider || 'serp',
    title: h.name || 'Hotel',
    image: h.image,
    icon: <BedDouble size={20} />,
    rating: typeof h.rating === 'number' && h.rating > 0 ? h.rating : undefined,
    ribbon: h.hotel_class || undefined,
    price: h.rate_per_night,
    priceNote: h.rate_per_night ? 'per night' : undefined,
    sub: Array.isArray(h.amenities) ? h.amenities.slice(0, 4).join(' · ') : undefined,
    stats: compact([
      stat('Rating', typeof h.rating === 'number' && h.rating > 0 ? `${h.rating} ★` : undefined, h.reviews ? `${h.reviews} reviews` : undefined),
      stat('Night', h.rate_per_night),
      stat('Total', h.total_rate),
    ]),
    specs: compact([
      spec('Class', h.hotel_class),
      spec('Per night', h.rate_per_night),
      spec('Total stay', h.total_rate),
      spec('Reviews', h.reviews ? `${h.reviews}` : undefined),
      spec('Amenities', Array.isArray(h.amenities) ? h.amenities.join(', ') : undefined),
    ]),
    cta: h.link ? { label: 'Check rates', href: h.link } : undefined,
  }));
  return group(att.card_title || `Hotels${att.keyword ? ` — ${att.keyword}` : ''}`, <BedDouble size={12} />, tiles);
};

const placeDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.place_results) ? att.place_results : [];
  const tiles = list.map((p, i): DeckTile => {
    const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(
      [p.name, p.address].filter(Boolean).join(' '),
    )}`;
    return {
      id: `${p.name || 'place'}-${i}`,
      kind: 'Place',
      provider: att.provider || 'serp',
      title: p.name || 'Place',
      image: p.image,
      icon: <MapPinned size={20} />,
      rating: typeof p.rating === 'number' && p.rating > 0 ? p.rating : undefined,
      ribbon: p.open_state || undefined,
      price: p.price,
      sub: compact([p.category, p.address]).join(' — ') || undefined,
      stats: compact([
        stat('Rating', typeof p.rating === 'number' && p.rating > 0 ? `${p.rating} ★` : undefined, p.reviews ? `${p.reviews} reviews` : undefined),
        stat('Open', p.open_state),
        stat('Price', p.price),
      ]),
      specs: compact([
        spec('Category', p.category),
        spec('Address', p.address),
        spec('Hours', p.open_state),
        spec('Phone', p.phone),
      ]),
      cta: { label: p.website ? 'Website' : 'Open in Maps', href: p.website || mapsUrl },
      cta2: p.website ? { label: 'Maps', href: mapsUrl } : undefined,
    };
  });
  return group(att.card_title || `Places${att.keyword ? ` — ${att.keyword}` : ''}`, <MapPinned size={12} />, tiles);
};

const shopDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.shop_products) ? att.shop_products : [];
  const tiles = list.map((p, i): DeckTile => ({
    id: `${p.asin || p.name || 'product'}-${i}`,
    kind: 'Product',
    provider: att.provider || 'serp',
    title: p.name || 'Product',
    image: p.image,
    icon: <ShoppingCart size={20} />,
    rating: typeof p.rating === 'number' && p.rating > 0 ? p.rating : undefined,
    ribbon: p.prime ? 'PRIME' : undefined,
    price: typeof p.price === 'number' ? inr(p.price) : p.price || undefined,
    sub: compact([p.source, p.delivery]).join(' · ') || undefined,
    stats: compact([
      stat('Price', typeof p.price === 'number' ? inr(p.price) : p.price),
      stat('Rating', typeof p.rating === 'number' && p.rating > 0 ? `${p.rating} ★` : undefined, p.reviews ? `${p.reviews} reviews` : undefined),
      stat('Store', p.source),
    ]),
    specs: compact([
      spec('Store', p.source),
      spec('Delivery', p.delivery),
      spec('Prime', p.prime ? 'Yes' : undefined),
      spec('Reviews', p.reviews ? `${p.reviews}` : undefined),
    ]),
    cta: p.link ? { label: `View on ${p.source || 'store'}`, href: p.link } : undefined,
  }));
  return group(att.card_title || `Products${att.keyword ? ` — ${att.keyword}` : ''}`, <ShoppingCart size={12} />, tiles);
};

const videoDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.videos) ? att.videos : [];
  const tiles = list.map((v, i): DeckTile => {
    const watchUrl = v.link || (v.video_id ? `https://www.youtube.com/watch?v=${v.video_id}` : undefined);
    const views = typeof v.views === 'number' ? v.views.toLocaleString('en-IN') : undefined;
    return {
      id: String(v.video_id || v.title || i),
      kind: 'Video',
      provider: att.provider || 'serp',
      title: v.title || 'Video',
      image: v.image,
      icon: <PlayCircle size={20} />,
      ribbon: v.length || undefined,
      sub: compact([v.channel, views && `${views} views`, v.published]).join(' · ') || undefined,
      stats: compact([stat('Length', v.length), stat('Views', views), stat('Published', v.published)]),
      specs: compact([
        spec('Channel', v.channel),
        spec('Views', views),
        spec('Published', v.published),
        spec('Length', v.length),
      ]),
      cta: watchUrl ? { label: 'Watch on YouTube', href: watchUrl } : undefined,
    };
  });
  return group(att.card_title || `Videos${att.keyword ? ` — ${att.keyword}` : ''}`, <PlayCircle size={12} />, tiles);
};

const newsDeck = (att: Attachment): DeckGroup | null => {
  const list = Array.isArray(att.articles) ? att.articles : [];
  const tiles = list.map((a, i): DeckTile => ({
    id: String(a.link || a.title || i),
    kind: 'News',
    provider: att.provider || 'serp',
    title: a.title || 'Article',
    image: a.image,
    icon: <Newspaper size={20} />,
    sub: compact([a.source, a.date]).join(' · ') || undefined,
    stats: compact([stat('Source', a.source), stat('Published', a.date)]),
    specs: compact([spec('Source', a.source), spec('Published', a.date), spec('Headline', a.title)]),
    cta: a.link ? { label: 'Read article', href: a.link } : undefined,
  }));
  return group(att.card_title || `News${att.keyword ? ` — ${att.keyword}` : ''}`, <Newspaper size={12} />, tiles);
};

const BUILDERS: Record<string, (att: Attachment) => DeckGroup | null> = {
  restaurant_cards: restaurantDeck,
  menu_cards: menuDeck,
  dining_cards: diningDeck,
  movie_cards: movieDeck,
  theatre_cards: theatreDeck,
  show_cards: showDeck,
  event_cards: eventDeck,
  venue_cards: venueDeck,
  artist_cards: artistDeck,
  zepto_product_cards: zeptoDeck,
  instamart_product_cards: instamartDeck,
  kite_instrument_cards: kiteDeck,
  order_cards: orderDeck,
  coupon_cards: couponDeck,
  flight_cards: flightDeck,
  hotel_cards: hotelDeck,
  place_cards: placeDeck,
  shop_product_cards: shopDeck,
  video_cards: videoDeck,
  news_cards: newsDeck,
};

/** Returns a deck for multi-result attachments, or null when the classic card should render. */
export const deckForAttachment = (att: Attachment): DeckGroup | null => {
  const builder = BUILDERS[String(att.kind || '').toLowerCase()];
  if (!builder) return null;
  try {
    return builder(att);
  } catch {
    return null;
  }
};
