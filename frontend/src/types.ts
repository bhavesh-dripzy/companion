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

// Two providers currently emit chat cards. `provider` is stamped by the
// backend so the frontend can swap branding (Zomato red `#E23744` vs
// Swiggy orange `#FC8019`), label text ("Zomato" vs "Swiggy"), and the
// "Order now" / "Add to cart" prompts wired through the QuickAction
// popovers. Cards without a `provider` field (legacy turns from before
// this stamp landed, or a future third provider) fall back to a neutral
// slate styling — never a wrong-brand colour.
export type CardProvider = 'zomato' | 'swiggy';

export interface Attachment {
  kind: string;
  source?: string;
  // Provider tag for card-shaped attachments (menu_cards / restaurant_cards /
  // cart_card). See `CardProvider` above.
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
}

export interface Message {
  role: 'user' | 'assistant';
  content: string;
  created_at?: string;
  message_id?: string;
  meta?: string[];
  trace?: any[];
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
  // Cross-turn LRU of menu items the user has been shown this session.
  // Frontend echoes back as `session_menu` on the next request so the
  // planner can cite a literal `variant_id` for "yes plz add" without
  // a redundant menu fetch (or worse, hallucinating a stale vid).
  session_menu?: SessionMenuEntry[];
  // Current Zomato cart — replaced wholesale by each successful
  // `create_cart`, carried unchanged otherwise. Frontend echoes back so the
  // response LLM has ground truth for cart contents on follow-up turns.
  session_cart?: SessionCart | null;
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
