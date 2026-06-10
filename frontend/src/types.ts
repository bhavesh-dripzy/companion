export interface UserStatus {
  authenticated: boolean;
  username: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  username: string;
}

export interface ToolProvider {
  id: string;
  label: string;
  available: boolean;
  mcp_url?: string;
  custom?: boolean;
  tool_count?: number;
}

export interface Address {
  address_id: string;
  location_name: string;
  latitude?: number;
  longitude?: number;
}

// A single dish tile inside a `menu_cards` attachment. Mirrors
// `_build_menu_card` in pipeline.py — every field except `name` is
// optional because Zomato populates them inconsistently across
// restaurants (e.g. small chains rarely have ratings, image-less
// items get veg-coloured placeholder tiles, etc.).
export interface MenuCardItem {
  name: string;
  item_id?: string;
  variant_id?: string;
  price?: number;
  discounted_price?: number;
  image_url?: string;
  rating?: number;
  votes?: number;
  description?: string;
  // Derived from Zomato's `item_tags`: "veg" / "non_veg" / "egg".
  veg_kind?: "veg" | "non_veg" | "egg";
  category?: string;
}

// One featured dish inside a restaurant card. These are the addable
// previews Zomato surfaces inside every `get_restaurants_for_keyword`
// result, capped per restaurant by `_MAX_FEATURED_ITEMS_PER_RESTAURANT`
// in pipeline.py. The frontend renders these inline beneath the
// restaurant header so the user can spot a dish and ask "add the Veg
// Whopper" without opening the full menu first — the variant_id is
// already cached in `session_menu` next turn.
export interface RestaurantCardItem {
  name: string;
  variant_id?: string;
  item_id?: string;
  price?: number;
  min_price?: number;
  image_url?: string;
  // Derived from Zomato's `is_veg`: "veg" / "non_veg".
  veg_kind?: "veg" | "non_veg" | "egg";
}

// One restaurant tile inside a `restaurant_cards` attachment. Mirrors
// `_build_restaurant_card_entry` in pipeline.py — every field except
// `res_id` and `name` is optional because Zomato populates them
// inconsistently (small chains rarely have ratings, freshly listed
// places lack images, etc.).
export interface RestaurantCardEntry {
  res_id: string;
  name: string;
  rating?: number;
  // Number on Zomato (vote count), short text on Swiggy ("10K+").
  votes?: number | string;
  eta?: string;
  distance_km?: number;
  res_image?: string;
  res_offer?: string;
  serviceability_status?: string;
  chain_id?: number | string;
  // Only populated for Swiggy (their listing returns cuisines per row);
  // Zomato's keyword-search payload doesn't ship cuisines per result.
  cuisines?: string[];
  items?: RestaurantCardItem[];
}

// Three providers currently emit chat cards. `provider` is stamped by the
// backend so the frontend can swap branding (Zomato red `#E23744`, Swiggy
// orange `#FC8019`, District purple `#7C3AED`), label text, and the
// "Order now" / "Add to cart" / "Book table" prompts wired through the
// QuickAction popovers. Cards without a `provider` field (legacy turns
// from before this stamp landed) fall back to a neutral slate styling —
// never a wrong-brand colour.
export type CardProvider =
  | 'zomato'
  | 'swiggy'
  | 'district'
  | 'zepto'
  | 'instamart'
  | 'kite'
  | 'serp';

// ---------------------------------------------------------------------------
// District card entries. District spans three independent discovery
// surfaces (dining table reservations, movies/shows, events/venues/artists)
// plus a single-booking reservation snapshot. Each surface gets its own
// attachment kind so the chat-side renderer can pick the right layout;
// the backend stamps every district attachment with `provider: "district"`.
// ---------------------------------------------------------------------------
export interface DiningCardEntry {
  res_id: string;
  name: string;
  rating?: number;
  cuisines?: string[];
  locality?: string;
  price_for_two?: number | string;
  image_url?: string;
  bookable_via_agent?: boolean;
  distance?: number | string;
  web_url?: string;
}

export interface MovieCardEntry {
  movie_id: string;
  name: string;
  languages?: string[];
  genres?: string[];
  duration_minutes?: number;
  rating?: number;
  cbfc_certification?: string;
  release_date?: string;
  poster_url?: string;
  spotlight_video_url?: string;
  web_url?: string;
}

export interface TheatreCardEntry {
  theatre_id: string;
  name: string;
  locality?: string;
  distance?: number | string;
  image_url?: string;
  web_url?: string;
}

export interface ShowCardEntry {
  show_id: string;
  movie_id?: string;
  movie_name?: string;
  theatre_id?: string;
  theatre_name?: string;
  show_date?: string;
  show_start_time?: string;
  screen_type?: string;
  language?: string;
  min_price?: number;
  max_price?: number;
  seat_availability?: string | number;
  booking_url?: string;
  web_url?: string;
}

export interface EventCardEntry {
  event_id: string;
  name: string;
  description?: string;
  date_string?: string;
  venue_name?: string;
  locality?: string;
  city?: string;
  price_string?: string;
  offer_string?: string;
  image_url?: string;
  video_url?: string;
  web_url?: string;
  min_price?: number;
  distance_km?: number;
  tags?: string[];
  rating?: number;
  review_count?: number;
  is_dining_event?: boolean;
  is_activity?: boolean;
  is_play?: boolean;
  is_attraction?: boolean;
}

export interface VenueCardEntry {
  venue_id: string;
  name: string;
  city?: string;
  locality?: string;
  image_url?: string;
  web_url?: string;
  distance_km?: number;
  event_names?: string[];
  categories?: string[];
}

export interface ArtistCardEntry {
  artist_id: string;
  name: string;
  artist_type?: string;
  description?: string;
  image_url?: string;
  upcoming_event_count?: number;
  genres?: string[];
}

// ---------------------------------------------------------------------------
// Zepto card entries. Zepto's discovery surface is products (single-store
// grocery), so cards are flat rows with `pvid` + `spid` ids the planner
// uses verbatim on follow-up "add to cart" turns. Cart snapshot mirrors
// `SessionZeptoCart` below — same shape echoed back by the executor on
// every `view_cart` / `update_cart` step.
// ---------------------------------------------------------------------------
export interface ZeptoProductCardEntry {
  product_variant_id: string;
  store_product_id: string;
  name: string;
  // Already converted from paise → rupees in the backend executor so the
  // UI can format directly with no conversion ambiguity.
  price_inr?: number | null;
  // Pre-formatted "₹46" string when a price is available. Always trust
  // `price_inr` when present; this is just the display-ready fallback.
  price_text?: string | null;
  mrp_inr?: number | null;
  pack_size?: string | null;
  image_url?: string | null;
  available_quantity?: number | null;
  is_ad?: boolean;
  // Echoes the search query that produced this row (used by
  // `search_multiple_products` to group cards per query).
  query?: string | null;
}

export interface ZeptoCartItem {
  product_variant_id?: string | null;
  store_product_id?: string | null;
  quantity?: number | null;
  name?: string | null;
  price_inr?: number | null;
  price_text?: string | null;
  pack_size?: string | null;
  image_url?: string | null;
}

export interface ZeptoCart {
  is_empty?: boolean;
  items: ZeptoCartItem[];
  // Backend stores the live Zepto total in paise (same as the API);
  // multiply by 0.01 in the UI when rendering.
  grand_total_paise?: number | null;
  summary_text?: string | null;
}

// Instamart product / cart shapes — mirrors the Zepto types above but
// keyed on Instamart's ``spin_id`` (per-variant cart key). Products are
// flattened to one entry per variant (pack size) so each card maps 1:1
// to a cart line.
export interface InstamartProductCardEntry {
  spin_id: string;
  product_id?: string | null;
  parent_product_id?: string | null;
  name: string;
  brand?: string | null;
  quantity_description?: string | null;
  price_inr?: number | null;
  mrp_inr?: number | null;
  image_url?: string | null;
  in_stock?: boolean;
  is_promoted?: boolean;
  query?: string | null;
}

export interface InstamartCartItem {
  spin_id: string;
  quantity: number;
  name?: string | null;
  brand?: string | null;
  price_inr?: number | null;
  mrp_inr?: number | null;
  image_url?: string | null;
  quantity_description?: string | null;
}

export interface InstamartCart {
  is_empty?: boolean;
  items: InstamartCartItem[];
  // INR × 100. Synthesized executor-side when the server omits a total.
  grand_total_paise?: number | null;
  summary_text?: string | null;
  selected_address_id?: string | null;
}

// ---------------------------------------------------------------------------
// Kite (Zerodha trading) card entries. The kite provider surfaces a
// brokerage account, so cards are flat financial tables rather than the
// product grids the grocery providers ship. Every kite attachment is
// stamped ``provider: "kite"`` so the chat-side theme picker switches to
// Zerodha blue. All fields except identifiers are optional — the backend
// harvesters (``app/services/kite/mcp_executor.py``) coerce missing /
// malformed values to ``null`` defensively, so the UI must guard every
// numeric before formatting.
// ---------------------------------------------------------------------------

// One tradeable instrument tile — from ``search_instruments`` or the
// quote harvesters (``get_ltp`` / ``get_ohlc`` / ``get_quotes``). The
// quote harvesters stamp ``price_fetched_at`` (unix seconds) so the
// planner's staleness rule has a timestamp; the UI shows it as a freshness
// hint on the LTP chip.
export interface KiteInstrumentCardEntry {
  tradingsymbol: string;
  exchange?: string | null;
  instrument_token?: number | null;
  name?: string | null;
  last_price?: number | null;
  // Unix seconds. Present only on rows lifted from a live quote fetch.
  price_fetched_at?: number | null;
}

// One holdings row — a long-term (CNC) position in the demat account.
export interface KiteHoldingRow {
  tradingsymbol: string;
  exchange?: string | null;
  instrument_token?: number | null;
  quantity?: number | null;
  average_price?: number | null;
  last_price?: number | null;
  close_price?: number | null;
  pnl?: number | null;
  day_change?: number | null;
  day_change_percentage?: number | null;
  product?: string | null;
}

export interface KiteHoldings {
  holdings: KiteHoldingRow[];
  // Unix seconds stamped by the harvester when the snapshot was taken.
  fetched_at?: number | null;
}

// One positions row — an intraday (MIS) / carry-forward (NRML) open
// position, split into ``net`` and ``day`` buckets by the harvester.
export interface KitePositionRow {
  tradingsymbol: string;
  exchange?: string | null;
  instrument_token?: number | null;
  product?: string | null;
  quantity?: number | null;
  average_price?: number | null;
  last_price?: number | null;
  pnl?: number | null;
  m2m?: number | null;
  value?: number | null;
}

export interface KitePositions {
  net: KitePositionRow[];
  day: KitePositionRow[];
  fetched_at?: number | null;
}

// One open / pending order from ``get_orders``.
export interface KiteOpenOrder {
  order_id: string;
  status?: string | null;
  tradingsymbol?: string | null;
  exchange?: string | null;
  transaction_type?: string | null;
  quantity?: number | null;
  filled_quantity?: number | null;
  pending_quantity?: number | null;
  price?: number | null;
  average_price?: number | null;
  trigger_price?: number | null;
  order_type?: string | null;
  product?: string | null;
  variety?: string | null;
  order_timestamp?: string | null;
}

// The SAFETY-CRITICAL pending order ticket. The planner assembles this
// (LLM-side) and it is round-tripped verbatim as ``session_kite_order_ticket``
// every turn until the user confirms or cancels. The card renders EVERY
// field explicitly with no silent defaults so the user reviews the exact
// order before it is placed against a real-money account.
export interface KiteOrderTicket {
  tradingsymbol?: string | null;
  exchange?: string | null;
  transaction_type?: string | null;
  quantity?: number | null;
  order_type?: string | null;
  product?: string | null;
  variety?: string | null;
  price?: number | null;
  trigger_price?: number | null;
  last_price?: number | null;
  estimated_cost?: number | null;
  [key: string]: unknown;
}

// Margin / funds snapshot from ``get_margins``. The backend forwards
// whatever Kite returns (the equity/commodity ``available`` blocks), so
// the shape is opaque — the card renders available cash + a couple of key
// rows defensively.
export interface KiteMargin {
  [key: string]: unknown;
}

export interface ReservationCard {
  booking_id: string;
  res_id?: string;
  restaurant_name?: string;
  booking_date?: string;
  slot_time?: string;
  party_size?: number;
  status?: string;
  final_amount?: number;
  payment_status?: string;
  confirmation_url?: string;
  cancellation_url?: string;
  message?: string;
}

export interface Attachment {
  kind: string;
  source?: string;
  // Provider tag for card-shaped attachments (menu_cards / restaurant_cards /
  // cart_card / district cards). See `CardProvider` above.
  provider?: CardProvider;
  mime_type?: string;
  data_b64?: string;
  alt?: string;
  caption?: string;
  upi_link?: string;
  amount_inr?: number | string;
  oversize?: boolean;
  // menu_cards-specific fields. Populated only when `kind === "menu_cards"`;
  // the frontend's <MenuCardGrid /> reads these to render the tile grid
  // below the assistant bubble (Zomato or Swiggy — same component).
  restaurant_name?: string;
  res_id?: number | string;
  categories?: string[];
  items?: MenuCardItem[];
  // restaurant_cards-specific fields. Populated only when
  // `kind === "restaurant_cards"`; the frontend's <RestaurantCardGrid />
  // reads these to render the restaurant tile row.
  keyword?: string;
  restaurants?: RestaurantCardEntry[];
  // cart_card-specific field. Populated only when `kind === "cart_card"`;
  // the frontend's <CartCard /> reads this to render the live cart
  // receipt under the assistant bubble (auto-shown right after a
  // successful cart mutation, or on-demand when the response LLM signals
  // `show_cart: true` for "what's in my cart?" follow-up turns). The
  // snapshot is the same shape echoed back as `session_cart` on the next
  // request — see `SessionCart` above.
  cart?: SessionCart;
  // District-specific list payloads. Each attachment populates exactly
  // one of these depending on its `kind` — see the discriminator below.
  //   kind === "dining_cards"      → restaurants?  (DiningCardEntry[])
  //   kind === "movie_cards"       → movies?
  //   kind === "theatre_cards"     → theatres?
  //   kind === "show_cards"        → shows?
  //   kind === "event_cards"       → events?
  //   kind === "venue_cards"       → venues?
  //   kind === "artist_cards"      → artists?
  //   kind === "reservation_card"  → reservation?
  // We use distinct keys (instead of overloading `restaurants`) so a
  // single `Attachment` is unambiguous to the renderer.
  dining_restaurants?: DiningCardEntry[];
  movies?: MovieCardEntry[];
  theatres?: TheatreCardEntry[];
  shows?: ShowCardEntry[];
  events?: EventCardEntry[];
  venues?: VenueCardEntry[];
  artists?: ArtistCardEntry[];
  reservation?: ReservationCard;
  // Zepto-specific list payloads:
  //   kind === "zepto_product_cards"  → products  (ZeptoProductCardEntry[])
  //   kind === "zepto_cart_card"      → cart      (ZeptoCart)  — reuses the
  //     `cart` field above; discriminator is the `kind` tag, since `cart`
  //     can equally hold a Zomato `SessionCart` or a Zepto `ZeptoCart`.
  products?: ZeptoProductCardEntry[];
  zepto_cart?: ZeptoCart;
  // Instamart attachments — separate fields to avoid the same name-collision
  // we had to dodge for Zepto (the legacy ``items`` field is already taken by
  // menu cards, and ``cart`` is taken by Zomato/Swiggy ``SessionCart``).
  //   kind === "instamart_product_cards" → instamart_products (variant-level)
  //   kind === "instamart_cart_card"     → instamart_cart
  instamart_products?: InstamartProductCardEntry[];
  instamart_cart?: InstamartCart;
  // Kite attachments — each kind carries exactly one of these typed
  // fields (the backend's ``_collect_kite_attachments`` discriminates on
  // ``kind``):
  //   kind === "kite_instrument_cards" → instruments  (KiteInstrumentCardEntry[])
  //   kind === "kite_holdings_card"    → holdings      (KiteHoldings)
  //   kind === "kite_positions_card"   → positions     (KitePositions)
  //   kind === "kite_open_orders_card" → orders        (KiteOpenOrder[])
  //   kind === "kite_order_ticket_card"→ order_ticket  (KiteOrderTicket)
  //   kind === "kite_margin_card"      → margin        (KiteMargin)
  instruments?: KiteInstrumentCardEntry[];
  holdings?: KiteHoldings;
  positions?: KitePositions;
  orders?: KiteOpenOrder[];
  order_ticket?: KiteOrderTicket;
  margin?: KiteMargin;
  //   kind === "kite_gtt_card"         → gtts         (KiteGttRow[])
  //   kind === "kite_trades_card"      → trades       (KiteTradeRow[])
  //   kind === "kite_mf_holdings_card" → mf_holdings  (KiteMfRow[])
  gtts?: KiteGttRow[];
  trades?: KiteTradeRow[];
  mf_holdings?: KiteMfRow[];
  // Long-tail result cards (any provider; see ./components/ResultCards.tsx):
  //   kind === "order_cards"    → past_orders  (OrderCardEntry[])
  //   kind === "tracking_card"  → tracking     (TrackingSnapshot)
  //   kind === "coupon_cards"   → coupons      (CouponCardEntry[])
  //   kind === "slot_cards"     → slots        (+ res_name/booking_date/party_size)
  //   kind === "review_cards"   → reviews      (ReviewCardEntry[])
  //   kind === "info_card"      → info         (InfoCardPayload) — the generic
  //     fallback tile: ANY successful tool result without a bespoke card.
  past_orders?: OrderCardEntry[];
  tracking?: TrackingSnapshot;
  coupons?: CouponCardEntry[];
  slots?: SlotEntry[];
  res_name?: string;
  booking_date?: string;
  party_size?: number | string;
  reviews?: ReviewCardEntry[];
  info?: InfoCardPayload;
  // Serp data tiles (see ./components/SerpCards.tsx):
  //   kind === "flight_cards"       → flight_options (+ route/leg/lowest_price)
  //   kind === "hotel_cards"        → hotel_options
  //   kind === "place_cards"        → place_results
  //   kind === "shop_product_cards" → shop_products
  //   kind === "video_cards"        → videos
  flight_options?: FlightOption[];
  route?: string;
  leg?: string;
  lowest_price?: number;
  hotel_options?: HotelOption[];
  place_results?: PlaceResult[];
  shop_products?: ShopProduct[];
  videos?: VideoResult[];
}

export interface FlightLeg {
  from?: string;
  departs?: string;
  to?: string;
  arrives?: string;
  airline?: string;
  flight_number?: string;
}

export interface FlightOption {
  price?: number;
  total_duration_minutes?: number;
  stops?: number;
  airlines?: string[];
  legs?: FlightLeg[];
  airline_logo?: string;
  departure_token?: string;
  booking_token?: string;
}

export interface HotelOption {
  name?: string;
  rate_per_night?: string;
  total_rate?: string;
  hotel_class?: string;
  rating?: number;
  reviews?: number;
  amenities?: string[];
  image?: string;
  link?: string;
}

export interface PlaceResult {
  name?: string;
  rating?: number;
  reviews?: number;
  category?: string;
  address?: string;
  open_state?: string;
  phone?: string;
  price?: string;
  image?: string;
  website?: string;
}

export interface ShopProduct {
  name?: string;
  price?: string | number;
  rating?: number;
  reviews?: number;
  asin?: string;
  prime?: boolean;
  image?: string;
  link?: string;
}

export interface VideoResult {
  title?: string;
  channel?: string;
  views?: number;
  length?: string;
  published?: string;
  link?: string;
  image?: string;
}

export interface OrderCardEntry {
  order_id: string;
  status?: string;
  place_name?: string;
  date?: string;
  total?: number;
  total_text?: string;
  eta?: string;
  items?: { name: string; quantity?: number }[];
}

export interface TrackingSnapshot {
  status?: string;
  eta?: string;
  stage?: string;
  order_id?: string;
  place_name?: string;
}

export interface CouponCardEntry {
  code?: string;
  title?: string;
  description?: string;
  savings_text?: string;
}

export interface SlotEntry {
  time: string;
  label?: string;
  available?: boolean;
}

export interface ReviewCardEntry {
  critic?: string;
  publication?: string;
  rating?: string;
  snippet?: string;
}

export interface InfoCardPayload {
  title: string;
  rows: { label: string; value: string }[];
  lines?: string[];
}

export interface KiteGttRow {
  id?: number | string;
  tradingsymbol?: string | null;
  trigger_values?: number[];
  last_price?: number;
  transaction_type?: string;
  quantity?: number;
  price?: number;
  status?: string;
  gtt_type?: string;
}

export interface KiteTradeRow {
  tradingsymbol: string;
  exchange?: string;
  transaction_type?: string;
  quantity?: number;
  average_price?: number;
  fill_timestamp?: string;
  order_id?: string;
}

export interface KiteMfRow {
  fund: string;
  tradingsymbol?: string;
  quantity?: number;
  average_price?: number;
  last_price?: number;
  pnl?: number;
}

export interface Message {
  role: 'user' | 'assistant';
  content: string;
  created_at?: string;
  message_id?: string;
  meta?: string[];
  trace?: any[];
  // Tool-execution outcome for the turn (drives the ActivityTrail pill).
  toolsOk?: boolean;
  toolsError?: string;
  attachments?: Attachment[];
  // Provider stickiness signal — the backend's `routed_provider` for the
  // turn that produced this assistant message. Echoed back on the `history`
  // array of subsequent `/api/agent/query/` requests so the orchestrator's
  // `_extract_previous_provider` Priority-1 path fires and keeps the user
  // pinned to (e.g.) `swiggy_food` mid-cart instead of silently drifting
  // back to the `zomato` default on an ambiguous follow-up like "coke ad
  // fries regular". Without this echo the stickiness check falls back to
  // substring-scanning the assistant text for "swiggy"/"zomato" — which
  // almost never appears in natural Hinglish replies about Burger King.
  provider?: string;
}

export interface SessionRestaurant {
  res_id: string;
  name: string;
  rating?: number;
  eta?: string;
  distance_km?: number;
  serviceability_status?: string;
}

// Saved-address row carried across turns. The backend auto-fetches a
// user's Zomato addresses on the first food turn of the session, surfaces
// them as `session_addresses`, and expects the frontend to echo them back
// on subsequent turns so it can skip the MCP round-trip. Same contract as
// `SessionRestaurant`. `label` is the friendly label the backend builds
// (e.g. "Home — Sector 21, Gurgaon"); everything else rides along opaquely.
export interface SessionAddress {
  address_id: string;
  label?: string;
  address_line?: string;
  addressTag?: string;
  address_tag?: string;
  location_name?: string;
  [key: string]: unknown;
}

// One row in the per-session menu LRU. The executor caches up to
// `_MENU_ITEMS_PER_RESTAURANT` rows per restaurant fetched this session
// (currently 8) so the planner can resolve "add the second one" on a
// later turn without re-fetching the menu. The frontend MUST echo this
// array back on the next request — same contract as `session_restaurants`
// and `session_addresses`. Without the round-trip, the planner loses its
// variant_id memory the moment the user takes a turn without a menu
// fetch (e.g. "yes plz add") and ends up hallucinating a stale id, which
// Zomato rejects with "Variant ID … not found in restaurant menu".
export interface SessionMenuEntry {
  variant_id: string;
  item_name: string;
  res_id?: string;
  price?: number | null;
  discounted_price?: number | null;
  categories?: string[] | null;
  variants?: unknown[];
  addon_group_ids?: string[];
  addon_groups?: unknown[];
  [key: string]: unknown;
}

// Current Zomato cart snapshot. Single dict (not LRU) because Zomato carts
// are strictly single-restaurant: every successful `create_cart` REPLACES
// the prior cart server-side. Frontend echoes back as `session_cart` on the
// next request so the response LLM can describe what's actually in the cart
// without inferring it from chat history (which fails the moment the user
// adds an item from a different restaurant).
//
// Carries enough rich data (`description`, `image_url`, `item_tags`,
// `charge_breakdown`, `restaurant_rating`, `distance_in_km`, …) for the
// chat-side <ZomatoCart /> card to render directly from this snapshot —
// both right after a successful `create_cart` and on follow-up turns
// where the LLM says "show the cart" without re-fetching.
export interface SessionCartItemAddon {
  id?: string;
  group_id?: string;
  name: string;
  add_on_group_name?: string | null;
  quantity?: number | null;
}

export interface SessionCartItem {
  variant_id: string;
  item_id?: string | null;
  item_name: string;
  quantity?: number | null;
  price?: number | null;
  discounted_price?: number | null;
  description?: string | null;
  image_url?: string | null;
  item_tags?: string[];
  add_ons?: SessionCartItemAddon[];
}

export interface SessionCartTax {
  tax_amount: number;
  tax_type?: string;
  currency_code?: string;
}

export interface SessionCartBaseCharge {
  amount: number;
  discount?: number | null;
  charge_type?: string;
  taxes?: SessionCartTax[];
}

export interface SessionCartChargeBreakdown {
  base_charges?: SessionCartBaseCharge[];
  taxes?: SessionCartTax[];
}

export interface SessionCart {
  cart_id: string;
  res_id?: string | number | null;
  restaurant_name?: string | null;
  restaurant_rating?: number | null;
  distance_in_km?: number | null;
  items: SessionCartItem[];
  item_total?: number | null;
  final_amount?: number | null;
  payment_method_type?: string | null;
  eta?: string | null;
  charge_breakdown?: SessionCartChargeBreakdown | null;
  user_message?: string | null;
  shareable_link?: string | null;
  promo_code?: string | null;
  promo_discount_amount?: number | null;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// District session indexes — LRU-style memory of every discovery row the
// backend has shown the user this session. The frontend MUST echo these
// back on every /api/agent/query/ request so the planner can cite literal
// ids without re-running discovery tools. Same contract as
// `SessionRestaurant` / `SessionAddress`.
// ---------------------------------------------------------------------------
export interface SessionMovie {
  movie_id: string;
  name: string;
  language?: string | string[];
  genre?: string | string[];
  duration_minutes?: number;
  rating?: number;
  release_date?: string;
  cbfc_certification?: string;
  poster_url?: string;
  spotlight_video_url?: string;
  web_url?: string;
}

export interface SessionTheatre {
  theatre_id: string;
  name: string;
  locality?: string;
  distance?: number | string;
  image_url?: string;
  web_url?: string;
}

export interface SessionEvent {
  event_id: string;
  name: string;
  date_string?: string;
  venue_name?: string;
  locality?: string;
  city?: string;
  price_string?: string;
  min_price?: number;
  image_url?: string;
  web_url?: string;
}

export interface SessionVenue {
  venue_id: string;
  name: string;
  city?: string;
  locality?: string;
  distance?: number | string;
  image_url?: string;
  web_url?: string;
}

export interface SessionArtist {
  artist_id: string;
  name: string;
  artist_type?: string;
  genres?: string[];
  description?: string;
  image_url?: string;
  upcoming_event_count?: number;
}

// Zepto session-state echoes. `session_zepto_products` is an LRU of every
// product (name + pvid + spid + price/pack/image) the user has been shown.
// `session_zepto_addresses` mirrors `SessionAddress` but holds Zepto's
// own `addressId` values (the user's saved-address book is provider-
// specific — Zomato's address ids don't work on Zepto). `session_zepto_cart`
// is a single dict snapshot of the live cart, kept in sync with every
// `view_cart` / `update_cart` step (optimistic projection from the args
// for update_cart so the UI updates without a round-trip).
export interface SessionZeptoProduct extends ZeptoProductCardEntry {
  [key: string]: unknown;
}

export interface SessionZeptoAddress {
  address_id: string;
  label: string;
  address_line?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  [key: string]: unknown;
}

export type SessionZeptoCart = ZeptoCart;

// Instamart-flavoured session indexes — same contract as the Zepto ones
// above, keyed on Instamart's ``spin_id`` / ``address_id``.
export type SessionInstamartProduct = InstamartProductCardEntry;

export interface SessionInstamartAddress {
  address_id: string;
  label?: string | null;
  category?: string | null;
  address_line?: string | null;
  phone_number?: string | null;
}

export type SessionInstamartCart = InstamartCart;

// Kite session-state echoes. ``session_kite_instruments`` is an LRU of
// every instrument the user has been shown (so the planner can copy a
// literal tradingsymbol/exchange/instrument_token triple instead of
// re-running ``search_instruments``). ``session_kite_holdings`` /
// ``session_kite_positions`` are the latest portfolio snapshots (single
// dicts). ``session_kite_open_orders`` is the latest open-orders list, and
// ``session_kite_order_ticket`` is the pending UNCONFIRMED order ticket
// the planner assembled last turn — carried verbatim until the user
// confirms or cancels. Same round-trip contract as the other session
// indexes; the frontend echoes these untouched on every request.
export type SessionKiteInstrument = KiteInstrumentCardEntry;

export type SessionKiteHoldings = KiteHoldings;

export type SessionKitePositions = KitePositions;

export type SessionKiteOpenOrders = KiteOpenOrder;

export type SessionKiteOrderTicket = KiteOrderTicket;

export interface SessionReservation {
  booking_id: string;
  res_id?: string;
  restaurant_name?: string;
  booking_date?: string;
  slot_time?: string;
  party_size?: number;
  status?: string;
  final_amount?: number;
  payment_status?: string;
  confirmation_url?: string;
  cancellation_url?: string;
  message?: string;
}

// Backend's compact view of which address is being used this turn and
// where it came from. The chat UI doesn't render this directly — the
// response LLM uses `source` to decide whether to nudge the user about
// changing addresses (auto = first-time, mention it; others = silent).
export interface AddressContext {
  address_id?: string | null;
  label?: string | null;
  source?: "request" | "session_state" | "session_addresses" | "auto" | string | null;
  options_count?: number;
}

// Slim chat row returned by `/api/chats/` for the sidebar list. Backed by
// the same `companion-messages` Dynamo table (single-table design: rows
// keyed under `chat#<chat_id>`) — see `companion_api/messages_repo.py`.
// The frontend keeps an array of these in state and renders them in the
// SidebarRail, pinning the active one and persisting the active chat_id
// in localStorage so a reload lands the user back where they were.
export interface ChatSummary {
  chat_id: string;
  title: string;
  created_at?: string | null;
  updated_at?: string | null;
  last_message_at?: string | null;
  last_message_preview?: string | null;
  message_count?: number;
}

export interface QueryResponse {
  assistant_message: string;
  selected_tool_providers: string[];
  // Chat this turn was persisted under. Echoed on every request body
  // for the *next* turn so all messages in a conversation land under
  // the same `msg#<chat_id>#…` Dynamo prefix. Server may auto-create
  // and return a fresh id when the client omits it (lazy creation).
  chat_id?: string | null;
  // Single primary provider the orchestrator picked this turn (the
  // alphabetically-first entry in `selected_tool_providers` when multiple
  // ran). The frontend stores this on the assistant `Message` and echoes
  // it back on the next request's `history` so the orchestrator can keep
  // the user on the same provider mid-flow.
  routed_provider?: string | null;
  tool_execution: {
    ok: boolean;
    error?: string;
    error_code?: string;
  };
  observable_trace: any[];
  // Opaque carry-forward state from the backend. The frontend MUST echo
  // both of these back on the next /api/agent/query/ request — they are
  // how the executor remembers the user's cart, restaurant index, etc.
  // across HTTP turns. Without this echo, every multi-turn flow loses
  // __CART_ID__, the restaurant index, and the order id.
  session_state?: Record<string, string>;
  session_restaurants?: SessionRestaurant[];
  session_addresses?: SessionAddress[];
  // District event-surface indexes — same echo contract as
  // session_restaurants. See `SessionMovie` / `SessionTheatre` /
  // `SessionEvent` / `SessionVenue` / `SessionArtist` /
  // `SessionReservation` above.
  session_movies?: SessionMovie[];
  session_theatres?: SessionTheatre[];
  session_events?: SessionEvent[];
  session_venues?: SessionVenue[];
  session_artists?: SessionArtist[];
  session_reservation?: SessionReservation | null;
  // Cross-turn LRU of menu items the user has been shown this session.
  // Frontend echoes back as `session_menu` on the next request so the
  // planner can cite a literal `variant_id` for "yes plz add" without
  // a redundant menu fetch (or worse, hallucinating a stale vid).
  session_menu?: SessionMenuEntry[];
  // Current Zomato cart — replaced wholesale by each successful
  // `create_cart`, carried unchanged otherwise. Frontend echoes back so the
  // response LLM has ground truth for cart contents on follow-up turns.
  session_cart?: SessionCart | null;
  // Zepto session memory. Same echo contract as `session_restaurants` /
  // `session_addresses`: frontend round-trips these untouched so the
  // planner can copy literal Zepto ids on follow-up turns ("add the milk
  // we just looked at") without rerunning `search_products` (a fresh
  // search returns a different pvid for the same product, which silently
  // breaks the cart flow).
  session_zepto_products?: SessionZeptoProduct[];
  session_zepto_addresses?: SessionZeptoAddress[];
  session_zepto_cart?: SessionZeptoCart | null;
  session_instamart_products?: SessionInstamartProduct[];
  session_instamart_addresses?: SessionInstamartAddress[];
  session_instamart_cart?: SessionInstamartCart | null;
  // Kite session memory. Same echo contract as the other indexes — the
  // frontend round-trips these untouched so the planner can copy literal
  // instrument triples, read the latest portfolio snapshots, and resolve
  // a "yes, place it" confirmation against the pending order ticket
  // without re-deriving any of it from chat history.
  session_kite_instruments?: SessionKiteInstrument[];
  session_serp_flights?: FlightOption[];
  session_kite_holdings?: SessionKiteHoldings | null;
  session_kite_positions?: SessionKitePositions | null;
  session_kite_open_orders?: SessionKiteOpenOrders[];
  session_kite_order_ticket?: SessionKiteOrderTicket | null;
  address_context?: AddressContext | null;
  attachments?: Attachment[];
}

export interface McpStatus {
  status: string;
  detail: string;
  mcp_url?: string;
  diagnostics?: any;
  token_status?: any;
}

export interface GeoCoordinate {
  latitude: number;
  longitude: number;
  accuracyMeters?: number;
}

export type MapsTravelMode = "DRIVE" | "WALK" | "BICYCLE" | "TWO_WHEELER" | "TRANSIT";

export interface MapsRouteSummary {
  distance_meters: number;
  duration_seconds: number;
  distance_text: string;
  duration_text: string;
  polyline?: string | null;
  description?: string | null;
}

export interface MapsRouteResponse {
  status: "success";
  route: MapsRouteSummary;
}

export interface MapsStatusResponse {
  configured: boolean;
}

export type VoiceSessionState = 'inactive' | 'connecting' | 'active' | 'error';

export interface VoiceToolCallRequest {
  intent: string;
  history: Array<{ role: string; content: string }>;
  session_state?: Record<string, string>;
  chat_id?: string | null;
  // Live GPS from navigator.geolocation. The backend uses this when the user
  // did NOT name a place in their intent; when they did ("near Saket"), the
  // planner geocodes the named place and ignores this field.
  geo?: { latitude: number; longitude: number; accuracy_meters?: number };
  session_restaurants?: SessionRestaurant[];
  session_addresses?: SessionAddress[];
  session_cart?: SessionCart | null;
  session_menu?: SessionMenuEntry[];
  session_movies?: SessionMovie[];
  session_theatres?: SessionTheatre[];
  session_events?: SessionEvent[];
  session_venues?: SessionVenue[];
  session_artists?: SessionArtist[];
  session_reservation?: SessionReservation | null;
}

export interface VoiceToolCallResponse {
  success: boolean;
  result: string;
  error?: string | null;
  session_state?: Record<string, string>;
  session_cart?: SessionCart | null;
  session_restaurants?: SessionRestaurant[];
  session_menu?: SessionMenuEntry[];
  // Rich cards (menu_cards / restaurant_cards / cart_card) so the chat UI can
  // render them live during a voice conversation.
  attachments?: Attachment[];
  chat_id?: string | null;
}
