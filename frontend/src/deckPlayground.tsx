/**
 * Dev-only playground (served by `vite dev` at /deck-playground.html, never
 * bundled into the production build, which only includes index.html). Mounts
 * the real <AttachmentList/> with fixture payloads for every card kind so the
 * swipe deck can be exercised without auth or a live backend. Fired
 * quick-prompts land in the log strip at the top for assertion.
 */

import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import { AttachmentList } from './ChatLog';
import type { Attachment } from './types';

const img = (w: number, h: number, hex: string, text: string) =>
  `https://placehold.co/${w}x${h}/${hex}/ffffff.png?text=${encodeURIComponent(text)}`;

const FIXTURES: { name: string; att: Attachment }[] = [
  {
    name: 'restaurant_cards (zomato)',
    att: {
      kind: 'restaurant_cards',
      provider: 'zomato',
      keyword: 'burmese',
      restaurants: [
        {
          res_id: 'r1',
          name: 'Burma Burma',
          rating: 4.6,
          votes: 12400,
          eta: '35-40 min',
          distance_km: 2.1,
          res_image: img(600, 400, 'e23744', 'Burma Burma'),
          res_offer: 'Flat 20% OFF',
          cuisines: ['Burmese', 'Tea Room'],
          items: [
            { name: 'Khao Suey', price: 425, veg_kind: 'veg', variant_id: 'v1' },
            { name: 'Tea Leaf Salad', price: 365, veg_kind: 'veg', variant_id: 'v2' },
          ],
        },
        {
          res_id: 'r2',
          name: 'Nagarjuna',
          rating: 4.3,
          votes: 8200,
          eta: '25-30 min',
          distance_km: 1.4,
          res_offer: '₹125 OFF above ₹249',
          cost_for_two: '₹400 for two',
          locality: 'Residency Road',
          cuisines: ['Andhra', 'Biryani'],
          items: [{ name: 'Chicken Biryani', price: 320, veg_kind: 'non_veg', variant_id: 'v3' }],
        },
        {
          res_id: 'r3',
          name: 'Corner House',
          rating: 4.5,
          votes: 15600,
          eta: '20-25 min',
          distance_km: 0.9,
          cuisines: ['Desserts', 'Ice Cream'],
        },
      ],
    },
  },
  {
    name: 'menu_cards (swiggy)',
    att: {
      kind: 'menu_cards',
      provider: 'swiggy',
      restaurant_name: 'Burger King',
      categories: ['Whoppers', 'Sides'],
      items: [
        {
          name: 'Veg Whopper',
          variant_id: 'm1',
          price: 209,
          discounted_price: 179,
          rating: 4.2,
          votes: 980,
          veg_kind: 'veg',
          category: 'Whoppers',
          description: 'Flame-grilled veg patty with fresh lettuce, tomato and mayo.',
          image_url: img(400, 400, 'fc8019', 'Whopper'),
        },
        {
          name: 'Crispy Chicken Burger',
          variant_id: 'm2',
          price: 189,
          rating: 4.4,
          votes: 1500,
          veg_kind: 'non_veg',
          category: 'Burgers',
        },
        { name: 'Peri Peri Fries', variant_id: 'm3', price: 109, veg_kind: 'veg', category: 'Sides' },
      ],
    },
  },
  {
    name: 'flight_cards (serp)',
    att: {
      kind: 'flight_cards',
      provider: 'serp',
      route: 'BLR → DEL',
      leg: 'outbound',
      outbound_date: '2026-06-19',
      flight_options: [
        {
          price: 5240,
          total_duration_minutes: 165,
          stops: 0,
          airlines: ['IndiGo'],
          legs: [{ from: 'BLR', departs: '06:10', to: 'DEL', arrives: '08:55', airline: 'IndiGo', flight_number: '6E 204' }],
        },
        {
          price: 4980,
          total_duration_minutes: 250,
          stops: 1,
          airlines: ['Air India'],
          legs: [
            { from: 'BLR', departs: '07:20', to: 'HYD', arrives: '08:35', airline: 'Air India', flight_number: 'AI 521' },
            { from: 'HYD', departs: '09:30', to: 'DEL', arrives: '11:30', airline: 'Air India', flight_number: 'AI 887' },
          ],
        },
        {
          price: 6110,
          total_duration_minutes: 170,
          stops: 0,
          airlines: ['Vistara'],
          legs: [{ from: 'BLR', departs: '18:05', to: 'DEL', arrives: '20:55', airline: 'Vistara', flight_number: 'UK 864' }],
        },
      ],
    },
  },
  {
    name: 'video_cards (serp)',
    att: {
      kind: 'video_cards',
      keyword: 'UPI explained',
      videos: [
        {
          title: 'How UPI rewired India’s money',
          video_id: 'dQw4w9WgXcQ',
          channel: 'Backstage Economics',
          views: 1200000,
          length: '18:42',
          published: '3 weeks ago',
          image: img(640, 360, '991122', 'UPI Video'),
        },
        {
          title: 'NPCI: the switch behind every payment',
          video_id: 'abc12345xyz',
          channel: 'Moneycontrol',
          views: 410000,
          length: '12:05',
          published: '1 month ago',
        },
      ],
    },
  },
  {
    name: 'news_cards (serp)',
    att: {
      kind: 'news_cards',
      keyword: 'fintech',
      articles: [
        {
          title: 'RBI clears credit lines on UPI for fintechs',
          source: 'Mint',
          date: '2 hours ago',
          link: 'https://example.com/rbi-upi',
          image: img(640, 360, '2b2f72', 'Mint'),
        },
        {
          title: 'SEBI tightens F&O disclosure norms',
          source: 'Economic Times',
          date: '5 hours ago',
          link: 'https://example.com/sebi',
        },
      ],
    },
  },
  {
    name: 'zepto_product_cards',
    att: {
      kind: 'zepto_product_cards',
      provider: 'zepto',
      products: [
        {
          product_variant_id: 'z1',
          store_product_id: 's1',
          name: 'Amul Gold Milk 1L',
          price_inr: 66,
          mrp_inr: 68,
          pack_size: '2 × 1L',
          available_quantity: 14,
          image_url: img(400, 400, '7b2cfa', 'Milk'),
          query: 'milk',
        },
        {
          product_variant_id: 'z2',
          store_product_id: 's2',
          name: 'Britannia Brown Bread',
          price_inr: 55,
          pack_size: '400g',
          available_quantity: 6,
          query: 'bread',
        },
        {
          product_variant_id: 'z3',
          store_product_id: 's3',
          name: 'Farm Eggs (6 pcs)',
          price_inr: 54,
          mrp_inr: 60,
          pack_size: '6 pieces',
          is_ad: true,
          available_quantity: 20,
          query: 'eggs',
        },
      ],
    },
  },
  {
    name: 'instamart_product_cards',
    att: {
      kind: 'instamart_product_cards',
      provider: 'instamart',
      instamart_products: [
        {
          spin_id: 'i1',
          name: 'Taaza Toned Milk',
          brand: 'Amul',
          quantity_description: '500 ml',
          price_inr: 29,
          mrp_inr: 30,
          in_stock: true,
        },
        {
          spin_id: 'i2',
          name: 'Greek Yogurt Blueberry',
          brand: 'Epigamia',
          quantity_description: '90 g',
          price_inr: 60,
          in_stock: false,
        },
      ],
    },
  },
  {
    name: 'kite_instrument_cards',
    att: {
      kind: 'kite_instrument_cards',
      provider: 'kite',
      instruments: [
        { tradingsymbol: 'INFY', exchange: 'NSE', name: 'Infosys', last_price: 1642.3, price_fetched_at: 1781250000, instrument_token: 408065 },
        { tradingsymbol: 'TCS', exchange: 'NSE', name: 'Tata Consultancy Services', last_price: 4012.55, instrument_token: 2953217 },
        { tradingsymbol: 'HDFCBANK', exchange: 'NSE', name: 'HDFC Bank', last_price: 1689.9, instrument_token: 341249 },
      ],
    },
  },
  {
    name: 'event_cards (district)',
    att: {
      kind: 'event_cards',
      provider: 'district',
      events: [
        {
          event_id: 'e1',
          name: 'Prateek Kuhad — Silhouettes Tour',
          date_string: 'Sat, 27 Jun · 7 PM',
          venue_name: 'Phoenix Marketcity',
          locality: 'Whitefield',
          city: 'Bengaluru',
          price_string: '₹1,499 onwards',
          min_price: 1499,
          image_url: img(600, 400, '7c3aed', 'Prateek Kuhad'),
          tags: ['Indie', 'Live Music'],
          rating: 4.8,
          review_count: 4200,
          web_url: 'https://example.com/event',
        },
        {
          event_id: 'e2',
          name: 'Bengaluru Comedy Festival',
          date_string: 'Sun, 28 Jun · 6 PM',
          venue_name: 'Good Shepherd Auditorium',
          city: 'Bengaluru',
          min_price: 799,
          offer_string: 'HDFC 10% off',
        },
      ],
    },
  },
  {
    name: 'movie_cards (district)',
    att: {
      kind: 'movie_cards',
      provider: 'district',
      movies: [
        {
          movie_id: 'mv1',
          name: 'Kalki 2898 AD: Part Two',
          languages: ['Telugu', 'Hindi'],
          genres: ['Sci-Fi', 'Action'],
          duration_minutes: 168,
          rating: 8.4,
          cbfc_certification: 'UA',
          release_date: '2026-06-12',
          poster_url: img(400, 600, '2b2f72', 'Kalki 2'),
        },
        {
          movie_id: 'mv2',
          name: 'Jawan 2',
          languages: ['Hindi'],
          genres: ['Action', 'Thriller'],
          duration_minutes: 155,
          rating: 7.9,
          cbfc_certification: 'UA',
        },
      ],
    },
  },
  {
    name: 'hotel_cards (serp)',
    att: {
      kind: 'hotel_cards',
      keyword: 'Bengaluru luxury',
      hotel_options: [
        {
          name: 'The Leela Palace',
          rate_per_night: '₹18,400',
          total_rate: '₹36,800',
          hotel_class: '5-star',
          rating: 4.8,
          reviews: 2100,
          amenities: ['Pool', 'Spa', 'Breakfast', 'Gym'],
          image: img(640, 400, '15596b', 'Leela'),
          link: 'https://example.com/leela',
        },
        {
          name: 'Taj West End',
          rate_per_night: '₹16,900',
          hotel_class: '5-star',
          rating: 4.7,
          reviews: 1800,
          amenities: ['Pool', 'Heritage', 'Bar'],
          link: 'https://example.com/taj',
        },
      ],
    },
  },
  {
    name: 'place_cards (serp)',
    att: {
      kind: 'place_cards',
      keyword: 'speciality coffee',
      place_results: [
        {
          name: 'Third Wave Coffee',
          rating: 4.4,
          reviews: 3200,
          category: 'Cafe',
          address: '100 Ft Rd, Indiranagar',
          open_state: 'Open · closes 11 PM',
          price: '₹₹',
          image: img(600, 400, '1a9d6b', 'Coffee'),
        },
        {
          name: 'Blue Tokai Roastery',
          rating: 4.5,
          reviews: 2100,
          category: 'Roastery',
          address: 'Church St',
          open_state: 'Open',
          website: 'https://example.com/bluetokai',
        },
      ],
    },
  },
  {
    name: 'shop_product_cards (serp)',
    att: {
      kind: 'shop_product_cards',
      card_title: 'Price comparison — AirPods Pro 3',
      shop_products: [
        {
          name: 'Apple AirPods Pro 3',
          source: 'Amazon',
          delivery: 'Free delivery by tomorrow',
          price: 24999,
          rating: 4.6,
          reviews: 8800,
          prime: true,
          image: img(400, 400, '221b2e', 'AirPods'),
          link: 'https://example.com/airpods',
        },
        {
          name: 'Apple AirPods Pro 3',
          source: 'Croma',
          delivery: 'Pickup today',
          price: 25490,
          rating: 4.5,
          reviews: 1200,
          link: 'https://example.com/croma',
        },
      ],
    },
  },
  {
    name: 'order_cards',
    att: {
      kind: 'order_cards',
      provider: 'zomato',
      past_orders: [
        {
          order_id: 'ORD-9921',
          status: 'Delivered',
          place_name: 'Meghana Foods',
          date: 'Last Tuesday',
          total: 642,
          items: [
            { name: 'Boneless Biryani', quantity: 2 },
            { name: 'Raita', quantity: 1 },
          ],
        },
        { order_id: 'ORD-9874', status: 'Delivered', place_name: 'Corner House', date: '4 Jun', total: 380 },
      ],
    },
  },
  {
    name: 'coupon_cards',
    att: {
      kind: 'coupon_cards',
      provider: 'zomato',
      coupons: [
        { code: 'PARTY20', title: '20% off up to ₹120', description: 'On orders above ₹399', savings_text: 'Save ₹120' },
        { code: 'AXIS125', title: 'Flat ₹125 off', description: 'Axis Bank cards only', savings_text: 'Save ₹125' },
      ],
    },
  },
  {
    name: 'tracking_card (classic fallback — no deck)',
    att: {
      kind: 'tracking_card',
      provider: 'zomato',
      tracking: { status: 'Out for delivery', eta: '12 min', stage: 'on_the_way', order_id: 'ORD-9921', place_name: 'Meghana Foods' },
    },
  },
];

export const Playground: React.FC = () => {
  const [prompts, setPrompts] = useState<string[]>([]);

  return (
    <div className="mx-auto max-w-3xl px-4 pb-24">
      <div className="sticky top-0 z-[70] -mx-4 mb-6 border-b border-[#ece3d6] bg-[#fdfaf3]/95 px-4 py-3 backdrop-blur">
        <h1 className="font-display text-xl font-bold">Deck Playground</h1>
        <div id="prompt-log" className="mt-1 font-mono text-[11px] text-slate-600">
          {prompts.length === 0 ? 'No quick-prompts fired yet.' : prompts.map((p, i) => <div key={i}>→ {p}</div>)}
        </div>
      </div>
      {FIXTURES.map((f) => (
        <section key={f.name} data-fixture={f.att.kind} className="mb-10">
          <h2 className="mb-2 font-mono text-[11px] font-bold uppercase tracking-widest text-slate-400">{f.name}</h2>
          <AttachmentList
            attachments={[f.att]}
            onQuickPrompt={(p) => setPrompts((prev) => [...prev.slice(-4), p])}
          />
        </section>
      ))}
    </div>
  );
};

createRoot(document.getElementById('root')!).render(<Playground />);
