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

export interface Message {
  role: 'user' | 'assistant';
  content: string;
  meta?: string[];
  trace?: any[];
}

export interface SessionRestaurant {
  res_id: string;
  name: string;
  rating?: number;
  eta?: string;
  distance_km?: number;
  serviceability_status?: string;
}

export interface QueryResponse {
  assistant_message: string;
  selected_tool_providers: string[];
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
