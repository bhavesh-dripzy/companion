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

export interface Attachment {
  kind: string;
  source?: string;
  mime_type?: string;
  data_b64?: string;
  alt?: string;
  caption?: string;
  upi_link?: string;
  amount_inr?: number | string;
  oversize?: boolean;
}

export interface Message {
  role: 'user' | 'assistant';
  content: string;
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

export interface QueryResponse {
  assistant_message: string;
  selected_tool_providers: string[];
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
