import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence } from 'motion/react';
import { Login } from './Login';
import { SettingsModal } from './SettingsModal';
import { AppsModal } from './components/AppsModal';
import { AdminDashboard } from './AdminDashboard';
import { LeftSidebar } from './components/LeftSidebar';
import { ChatArea } from './components/ChatArea';
import { OrdersPopup } from './components/OrdersPopup';
import { CartDock } from './components/CartDock';
import { api, clearToken, clearRefreshToken, getToken, streamPost } from './api';
import type { StreamSegment } from './types';
import { lastKnownUser, prefetchHeroGreeting, rememberLastUser } from './heroGreeting';
import { InstamartAddressProvider, type InstamartSavedAddress } from './InstamartAddressContext';
import { McpProviderProvider } from './McpProviderContext';
import { ZeptoAddressProvider, type ZeptoSavedAddress } from './ZeptoAddressContext';
import type {
  ChatSummary,
  GeoCoordinate,
  McpStatus,
  Message,
  QueryResponse,
  SessionAddress,
  SessionArtist,
  SessionCart,
  SessionEvent,
  SessionMenuEntry,
  SessionMovie,
  SessionReservation,
  SessionRestaurant,
  SessionTheatre,
  SessionVenue,
  SessionZeptoAddress,
  SessionZeptoCart,
  SessionZeptoProduct,
  SessionInstamartAddress,
  SessionInstamartCart,
  SessionInstamartProduct,
  SessionKiteHoldings,
  SessionKiteInstrument,
  FlightOption,
  SessionKiteOpenOrders,
  SessionKiteOrderTicket,
  SessionKitePositions,
  ToolProvider,
} from './types';
import { useUserLocation } from './useUserLocation';
import { mapsStatus } from './maps';
import './index.css';

// localStorage key holding the chat_id the user was last viewing. We
// restore it on reload so the workspace lands back in the same
// conversation; it gets cleared by "new chat" / logout, and overwritten
// every time the backend confirms (or lazy-creates) a chat_id.
const ACTIVE_CHAT_STORAGE_KEY = 'companion.active_chat_id';

// Path-based chat routing: the active chat lives in the URL as `/c/<chat_id>`
// (shareable, refresh-safe, back/forward-aware). `localStorage` stays the
// cold-start fallback — in the packaged (Capacitor) app the webview boots at the
// bundle root with no path, so the last-active chat is restored from storage.
function chatIdFromPath(): string | null {
  const match = window.location.pathname.match(/^\/c\/([^/?#]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function chatPathFor(chatId: string | null): string {
  return chatId ? `/c/${encodeURIComponent(chatId)}` : '/';
}

// Per-chat session snapshot. Mirrors the `session_*` fields the backend
// round-trips on every /api/agent/query/ turn — restaurants the user
// has been shown, the LRU of menu items (variant_ids needed to resolve
// "yes add the second one"), saved delivery addresses, the active cart,
// and the placeholder-id dictionary (__CART_ID__, __RESTAURANT_ID__, …).
//
// We persist this blob under `companion.chat.<chat_id>.session` so a
// page refresh — or switching to another chat and back — restores the
// same conversational context the backend was relying on.
const CHAT_SESSION_KEY_PREFIX = 'companion.chat.';

interface PersistedChatSession {
  session_state: Record<string, string>;
  session_restaurants: SessionRestaurant[];
  session_addresses: SessionAddress[];
  session_menu: SessionMenuEntry[];
  session_cart: SessionCart | null;
  // District event-surface memory. Same echo contract as
  // session_restaurants — frontend round-trips whatever the prior turn
  // returned so the planner can cite literal movie / theatre / event /
  // venue / artist / booking ids without re-running discovery.
  session_movies: SessionMovie[];
  session_theatres: SessionTheatre[];
  session_events: SessionEvent[];
  session_venues: SessionVenue[];
  session_artists: SessionArtist[];
  session_reservation: SessionReservation | null;
  // Zepto session memory — products LRU, saved-address book, live cart.
  // Same persistence contract as the other indexes: stored locally so a
  // reload restores the planner's context (otherwise the user has to
  // re-search for the same milk after every page refresh).
  session_zepto_products: SessionZeptoProduct[];
  session_zepto_addresses: SessionZeptoAddress[];
  session_zepto_cart: SessionZeptoCart | null;
  session_instamart_products: SessionInstamartProduct[];
  session_instamart_addresses: SessionInstamartAddress[];
  session_instamart_cart: SessionInstamartCart | null;
  // The Instamart delivery address the user picked this session. Must round-trip
  // so cart/checkout reuse it instead of re-resolving the nearest each turn.
  selected_instamart_address_id: string | null;
  // Kite (Zerodha trading) session memory — instrument LRU, the latest
  // holdings / positions snapshots, the open-orders list, and the pending
  // unconfirmed order ticket. Same persistence contract as the other
  // indexes: stored locally so a reload restores the planner's context.
  session_kite_instruments: SessionKiteInstrument[];
  session_serp_flights: FlightOption[];
  session_serp_products: Record<string, unknown>[];
  session_serp_places: Record<string, unknown>[];
  session_serp_hotels: Record<string, unknown>[];
  session_kite_holdings: SessionKiteHoldings | null;
  session_kite_positions: SessionKitePositions | null;
  session_kite_open_orders: SessionKiteOpenOrders[];
  session_kite_order_ticket: SessionKiteOrderTicket | null;
}

const chatSessionKey = (chatId: string) => `${CHAT_SESSION_KEY_PREFIX}${chatId}.session`;

const loadChatSession = (chatId: string): PersistedChatSession | null => {
  try {
    const raw = localStorage.getItem(chatSessionKey(chatId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    return {
      session_state:
        parsed.session_state && typeof parsed.session_state === 'object'
          ? (parsed.session_state as Record<string, string>)
          : {},
      session_restaurants: Array.isArray(parsed.session_restaurants)
        ? (parsed.session_restaurants as SessionRestaurant[])
        : [],
      session_addresses: Array.isArray(parsed.session_addresses)
        ? (parsed.session_addresses as SessionAddress[])
        : [],
      session_menu: Array.isArray(parsed.session_menu)
        ? (parsed.session_menu as SessionMenuEntry[])
        : [],
      session_cart:
        parsed.session_cart
        && typeof parsed.session_cart === 'object'
        && (parsed.session_cart as SessionCart).cart_id
          ? (parsed.session_cart as SessionCart)
          : null,
      session_movies: Array.isArray(parsed.session_movies)
        ? (parsed.session_movies as SessionMovie[])
        : [],
      session_theatres: Array.isArray(parsed.session_theatres)
        ? (parsed.session_theatres as SessionTheatre[])
        : [],
      session_events: Array.isArray(parsed.session_events)
        ? (parsed.session_events as SessionEvent[])
        : [],
      session_venues: Array.isArray(parsed.session_venues)
        ? (parsed.session_venues as SessionVenue[])
        : [],
      session_artists: Array.isArray(parsed.session_artists)
        ? (parsed.session_artists as SessionArtist[])
        : [],
      session_reservation:
        parsed.session_reservation
        && typeof parsed.session_reservation === 'object'
        && (parsed.session_reservation as SessionReservation).booking_id
          ? (parsed.session_reservation as SessionReservation)
          : null,
      session_zepto_products: Array.isArray(parsed.session_zepto_products)
        ? (parsed.session_zepto_products as SessionZeptoProduct[])
        : [],
      session_zepto_addresses: Array.isArray(parsed.session_zepto_addresses)
        ? (parsed.session_zepto_addresses as SessionZeptoAddress[])
        : [],
      session_zepto_cart:
        parsed.session_zepto_cart && typeof parsed.session_zepto_cart === 'object'
          ? (parsed.session_zepto_cart as SessionZeptoCart)
          : null,
      session_instamart_products: Array.isArray(parsed.session_instamart_products)
        ? (parsed.session_instamart_products as SessionInstamartProduct[])
        : [],
      session_instamart_addresses: Array.isArray(parsed.session_instamart_addresses)
        ? (parsed.session_instamart_addresses as SessionInstamartAddress[])
        : [],
      session_instamart_cart:
        parsed.session_instamart_cart && typeof parsed.session_instamart_cart === 'object'
          ? (parsed.session_instamart_cart as SessionInstamartCart)
          : null,
      selected_instamart_address_id:
        typeof parsed.selected_instamart_address_id === 'string'
          ? parsed.selected_instamart_address_id
          : null,
      session_kite_instruments: Array.isArray(parsed.session_kite_instruments)
        ? (parsed.session_kite_instruments as SessionKiteInstrument[])
        : [],
      session_serp_flights: Array.isArray(parsed.session_serp_flights)
        ? (parsed.session_serp_flights as FlightOption[])
        : [],
      session_serp_products: Array.isArray(parsed.session_serp_products)
        ? (parsed.session_serp_products as Record<string, unknown>[])
        : [],
      session_serp_places: Array.isArray(parsed.session_serp_places)
        ? (parsed.session_serp_places as Record<string, unknown>[])
        : [],
      session_serp_hotels: Array.isArray(parsed.session_serp_hotels)
        ? (parsed.session_serp_hotels as Record<string, unknown>[])
        : [],
      session_kite_holdings:
        parsed.session_kite_holdings && typeof parsed.session_kite_holdings === 'object'
          ? (parsed.session_kite_holdings as SessionKiteHoldings)
          : null,
      session_kite_positions:
        parsed.session_kite_positions && typeof parsed.session_kite_positions === 'object'
          ? (parsed.session_kite_positions as SessionKitePositions)
          : null,
      session_kite_open_orders: Array.isArray(parsed.session_kite_open_orders)
        ? (parsed.session_kite_open_orders as SessionKiteOpenOrders[])
        : [],
      session_kite_order_ticket:
        parsed.session_kite_order_ticket && typeof parsed.session_kite_order_ticket === 'object'
          ? (parsed.session_kite_order_ticket as SessionKiteOrderTicket)
          : null,
    };
  } catch {
    return null;
  }
};

const saveChatSession = (chatId: string, snap: PersistedChatSession) => {
  try {
    const isEmpty =
      Object.keys(snap.session_state).length === 0
      && snap.session_restaurants.length === 0
      && snap.session_addresses.length === 0
      && snap.session_menu.length === 0
      && snap.session_cart === null
      && snap.session_movies.length === 0
      && snap.session_theatres.length === 0
      && snap.session_events.length === 0
      && snap.session_venues.length === 0
      && snap.session_artists.length === 0
      && snap.session_reservation === null
      && snap.session_zepto_products.length === 0
      && snap.session_zepto_addresses.length === 0
      && snap.session_zepto_cart === null
      && snap.session_kite_instruments.length === 0
      && snap.session_serp_flights.length === 0
      && snap.session_serp_products.length === 0
      && snap.session_serp_places.length === 0
      && snap.session_serp_hotels.length === 0
      && snap.session_kite_holdings === null
      && snap.session_kite_positions === null
      && snap.session_kite_open_orders.length === 0
      && snap.session_kite_order_ticket === null;
    if (isEmpty) {
      localStorage.removeItem(chatSessionKey(chatId));
      return;
    }
    localStorage.setItem(chatSessionKey(chatId), JSON.stringify(snap));
  } catch (err) {
    console.warn('Failed to persist chat session', err);
  }
};

const clearChatSession = (chatId: string) => {
  try {
    localStorage.removeItem(chatSessionKey(chatId));
  } catch {
    /* noop */
  }
};

const clearAllChatSessions = () => {
  try {
    const keys: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i);
      if (k && k.startsWith(CHAT_SESSION_KEY_PREFIX)) keys.push(k);
    }
    keys.forEach((k) => localStorage.removeItem(k));
  } catch {
    /* noop */
  }
};

// Same disclaimer as INITIAL_REMINDERS — there's no /api/orders route,
// these are visual stubs only. The real orders flow happens through
// the chat (Zomato/Swiggy carts) and the cart_card attachment.
const INITIAL_ORDERS = [
  { id: 'ord-d1', category: 'daily', provider: 'Swiggy Daily', text: 'Fresh low-fat milk bottle & organic whole bread', status: 'Active Subscription', time: 'Daily 7 AM', price: '₹120' },
  { id: 'ord-d2', category: 'daily', provider: 'Fruits Routine', text: 'Seasonal mixed seed & avocado fitness combo', status: 'Paused', time: 'Daily 9 AM', price: '₹240' },
  { id: 'ord-t1', category: 'today', provider: 'Zomato Food', text: 'Sourdough Woodfired Pepperoni Pizza x1', status: 'In Transit', time: 'ETA 12 mins', price: '₹480' },
  { id: 'ord-t2', category: 'today', provider: 'Zepto Quick', text: 'Salted Butter, Fresh Bananas & Greek Yogurt', status: 'Preparing', time: 'ETA 8 mins', price: '₹210' },
  { id: 'ord-t3', category: 'today', provider: 'Blinkit Spot', text: 'Emergency charging cord & cell lithium batteries', status: 'Delivered', time: 'Delivered today', price: '₹350' },
];

const ADMIN_USERNAMES = new Set(['admin', 'admin_bhavesh', 'admin_kshitij', 'local_admin']);

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(!!getToken());
  const [username, setUsername] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);

  const [messages, setMessages] = useState<Message[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [pendingLocationQuery, setPendingLocationQuery] = useState<string | null>(null);

  const [toolProviders, setToolProviders] = useState<ToolProvider[]>([]);
  const [mcpStatuses, setMcpStatuses] = useState<Record<string, McpStatus>>({});
  const [isLoadingMcp, setIsLoadingMcp] = useState(false);
  const [gcalStatus, setGcalStatus] = useState<{ connected: boolean; configured: boolean } | null>(null);
  const [mapsConfigured, setMapsConfigured] = useState<boolean | null>(null);

  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isAdminOpen, setIsAdminOpen] = useState(false);
  const [settingsInitialTab, setSettingsInitialTab] = useState<'mcp' | 'calendar' | 'models'>('mcp');
  const [settingsInitialProvider, setSettingsInitialProvider] = useState<string | undefined>(undefined);

  // Left sidebar is hideable on every viewport; on mobile it slides
  // in over the chat surface, on desktop the column collapses to a
  // 0-width hidden state and a Menu button surfaces in the ChatArea
  // header to bring it back. Start collapsed on phones so the hero
  // empty-state gets the full width instead of being crushed into the
  // ~55px left over beside the 320px sidebar.
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(
    () => typeof window !== 'undefined' && window.innerWidth < 768,
  );

  // On phones the sidebar floats over the chat, so any navigation that
  // changes what's underneath should dismiss it (otherwise the drawer
  // hides the chat the user just opened). No-op on desktop.
  const closeSidebarOnMobile = useCallback(() => {
    if (typeof window !== 'undefined' && window.innerWidth < 768) {
      setIsSidebarCollapsed(true);
    }
  }, []);

  // Cosmetic popups (no backend wiring) — kept local-only as confirmed
  // up-front. Users can toggle items / add / delete entries and the
  // state lives for the session.
  const [showApps, setShowApps] = useState(false);
  // When set, the Apps modal opens straight to this provider's auth/connect view
  // (fired by tapping a not-connected "Search in" chip in the grocery picker).
  const [appsInitialProvider, setAppsInitialProvider] = useState<string | null>(null);
  const [showOrdersPopup, setShowOrdersPopup] = useState(false);
  const [orders, setOrders] = useState(INITIAL_ORDERS);

  // Carry-forward state from the backend across turns. See the inline
  // notes in the previous skin: each successful /api/agent/query/ ships
  // the placeholder IDs (cart_id, restaurant_id, order_id) the executor
  // learned this turn and the LRU index of restaurants the user has
  // been shown. We echo both back on the next request so the backend
  // can resolve __CART_ID__ for checkout, skip a re-search when the
  // user picks a known restaurant by name, etc.
  const [sessionState, setSessionState] = useState<Record<string, string>>({});
  const [sessionRestaurants, setSessionRestaurants] = useState<SessionRestaurant[]>([]);
  const [sessionAddresses, setSessionAddresses] = useState<SessionAddress[]>([]);
  const [sessionMenu, setSessionMenu] = useState<SessionMenuEntry[]>([]);
  const [sessionCart, setSessionCart] = useState<SessionCart | null>(null);
  // District event-surface memory — same echo contract as the food
  // surfaces above. Round-tripped on every /api/agent/query/ so the
  // planner can pick a literal movie/theatre/event/venue/artist id
  // without re-running discovery, and the response LLM can describe
  // an active reservation without re-fetching it.
  const [sessionMovies, setSessionMovies] = useState<SessionMovie[]>([]);
  const [sessionTheatres, setSessionTheatres] = useState<SessionTheatre[]>([]);
  const [sessionEvents, setSessionEvents] = useState<SessionEvent[]>([]);
  const [sessionVenues, setSessionVenues] = useState<SessionVenue[]>([]);
  const [sessionArtists, setSessionArtists] = useState<SessionArtist[]>([]);
  const [sessionReservation, setSessionReservation] = useState<SessionReservation | null>(null);
  // Delivery address the user pinned via the AddressChip; threaded as
  // address_id on every agent request and as the carried __ADDRESS_ID__.
  const [selectedAddressId, setSelectedAddressId] = useState<string | null>(null);
  // Provider of the turn that produced the live food cart (drives CartDock
  // branding + the right checkout prompt).
  const [foodCartProvider, setFoodCartProvider] = useState<string | null>(null);
  // Zepto session memory. Same echo contract as the other indexes —
  // round-trip on every /api/agent/query/ so the planner can re-use
  // literal pvid/spid ids and the chat surface keeps the cart in sync
  // with what the executor projected after the last update_cart call.
  const [sessionZeptoProducts, setSessionZeptoProducts] = useState<SessionZeptoProduct[]>([]);
  const [sessionZeptoAddresses, setSessionZeptoAddresses] = useState<SessionZeptoAddress[]>([]);
  const [sessionZeptoCart, setSessionZeptoCart] = useState<SessionZeptoCart | null>(null);
  const [sessionInstamartProducts, setSessionInstamartProducts] = useState<SessionInstamartProduct[]>([]);
  const [sessionInstamartAddresses, setSessionInstamartAddresses] = useState<SessionInstamartAddress[]>([]);
  const [sessionInstamartCart, setSessionInstamartCart] = useState<SessionInstamartCart | null>(null);
  const [selectedInstamartAddressId, setSelectedInstamartAddressId] = useState<string | null>(null);
  // Full Swiggy saved-address list (same source as the Apps modal). The product
  // card attachment only carries the opaque selected id, so we fetch the list
  // here and resolve the label in the delivery-address bar via context.
  const [instamartSavedAddresses, setInstamartSavedAddresses] = useState<InstamartSavedAddress[]>([]);
  // Zepto saved-address list + selected id (mirrors Instamart). select_saved_address
  // binds the store + order doorstep; shown in the "Delivering to" bar below cards.
  const [zeptoSavedAddresses, setZeptoSavedAddresses] = useState<ZeptoSavedAddress[]>([]);
  const [selectedZeptoAddressId, setSelectedZeptoAddressId] = useState<string | null>(null);
  // Kite (Zerodha trading) session memory. Same echo contract as the other
  // indexes — round-trip on every /api/agent/query/ so the planner can
  // re-use literal instrument triples, read the latest portfolio snapshots,
  // and resolve a confirmation against the pending order ticket.
  const [sessionKiteInstruments, setSessionKiteInstruments] = useState<SessionKiteInstrument[]>([]);
  const [sessionSerpFlights, setSessionSerpFlights] = useState<FlightOption[]>([]);
  const [sessionSerpProducts, setSessionSerpProducts] = useState<Record<string, unknown>[]>([]);
  const [sessionSerpPlaces, setSessionSerpPlaces] = useState<Record<string, unknown>[]>([]);
  const [sessionSerpHotels, setSessionSerpHotels] = useState<Record<string, unknown>[]>([]);
  const [sessionKiteHoldings, setSessionKiteHoldings] = useState<SessionKiteHoldings | null>(null);
  const [sessionKitePositions, setSessionKitePositions] = useState<SessionKitePositions | null>(null);
  const [sessionKiteOpenOrders, setSessionKiteOpenOrders] = useState<SessionKiteOpenOrders[]>([]);
  const [sessionKiteOrderTicket, setSessionKiteOrderTicket] = useState<SessionKiteOrderTicket | null>(null);

  // Multi-chat support — same contract as before. `chatId === null`
  // means "fresh new chat about to be started" — no Dynamo row exists
  // yet; the first /api/agent/query/ call will lazy-create one and
  // return its id, which we'll then pin.
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatId, setChatId] = useState<string | null>(
    () => chatIdFromPath() || localStorage.getItem(ACTIVE_CHAT_STORAGE_KEY) || null,
  );
  // The new agent backend (/api/agent/turn) keeps conversation history server-side and is
  // SEPARATE from the old /api/chats persistence. Track its conversation id in a ref so it
  // never feeds the chat-list/URL machinery — which, not finding it in /api/chats, would
  // clear the chat (the root cause of the "chat disappeared" bug).
  const agentChatIdRef = useRef<string | null>(null);

  const { location: userLocation } = useUserLocation();
  // Persists geo across turns once the user grants it (hook only auto-captures on mount).
  const [grantedGeo, setGrantedGeo] = useState<import('./types').GeoCoordinate | null>(null);
  const effectiveGeo = grantedGeo ?? userLocation;

  useEffect(() => {
    const checkAuth = async () => {
      if (!getToken()) {
        setLoading(false);
        return;
      }
      // Greeting fetch races the auth round-trip so the hero is usually
      // ready by the time ChatArea mounts.
      const guessUser = lastKnownUser();
      if (guessUser) prefetchHeroGreeting(guessUser);
      try {
        const data = await api.get('/api/auth/status/');
        setUsername(data.username);
        rememberLastUser(data.username);
        if (data.username !== guessUser) prefetchHeroGreeting(data.username);
        setIsAuthenticated(true);
        loadInitialData();
      } catch (err) {
        setIsAuthenticated(false);
        clearToken();
      } finally {
        setLoading(false);
      }
    };

    checkAuth();

    const handleAuthFailure = () => {
      setIsAuthenticated(false);
      setUsername('');
    };

    window.addEventListener('auth-failure', handleAuthFailure);
    return () => window.removeEventListener('auth-failure', handleAuthFailure);
  }, []);

  // Per-chat session persistence. Mirrors every change to the
  // round-tripped session_* fields into localStorage under the current
  // chat's key so a page refresh restores the same conversational
  // context (menu LRU, restaurants, cart, addresses, placeholder ids).
  useEffect(() => {
    if (!chatId) return;
    saveChatSession(chatId, {
      session_state: sessionState,
      session_restaurants: sessionRestaurants,
      session_addresses: sessionAddresses,
      session_menu: sessionMenu,
      session_cart: sessionCart,
      session_movies: sessionMovies,
      session_theatres: sessionTheatres,
      session_events: sessionEvents,
      session_venues: sessionVenues,
      session_artists: sessionArtists,
      session_reservation: sessionReservation,
      session_zepto_products: sessionZeptoProducts,
      session_zepto_addresses: sessionZeptoAddresses,
      session_zepto_cart: sessionZeptoCart,
      session_instamart_products: sessionInstamartProducts,
      session_instamart_addresses: sessionInstamartAddresses,
      session_instamart_cart: sessionInstamartCart,
      selected_instamart_address_id: selectedInstamartAddressId,
      session_kite_instruments: sessionKiteInstruments,
      session_serp_flights: sessionSerpFlights,
      session_serp_products: sessionSerpProducts,
      session_serp_places: sessionSerpPlaces,
      session_serp_hotels: sessionSerpHotels,
      session_kite_holdings: sessionKiteHoldings,
      session_kite_positions: sessionKitePositions,
      session_kite_open_orders: sessionKiteOpenOrders,
      session_kite_order_ticket: sessionKiteOrderTicket,
    });
  }, [
    chatId,
    sessionState,
    sessionRestaurants,
    sessionAddresses,
    sessionMenu,
    sessionCart,
    sessionMovies,
    sessionTheatres,
    sessionEvents,
    sessionVenues,
    sessionArtists,
    sessionReservation,
    sessionZeptoProducts,
    sessionZeptoAddresses,
    sessionZeptoCart,
    sessionInstamartProducts,
    sessionInstamartAddresses,
    sessionInstamartCart,
    selectedInstamartAddressId,
    sessionKiteInstruments,
    sessionSerpFlights,
    sessionSerpProducts,
    sessionSerpPlaces,
    sessionSerpHotels,
    sessionKiteHoldings,
    sessionKitePositions,
    sessionKiteOpenOrders,
    sessionKiteOrderTicket,
  ]);

  const loadInitialData = async () => {
    await refreshChatList();
    if (chatId) {
      const snap = loadChatSession(chatId);
      if (snap) {
        setSessionState(snap.session_state);
        setSessionRestaurants(snap.session_restaurants);
        setSessionAddresses(snap.session_addresses);
        setSessionMenu(snap.session_menu);
        setSessionCart(snap.session_cart);
        setSessionMovies(snap.session_movies);
        setSessionTheatres(snap.session_theatres);
        setSessionEvents(snap.session_events);
        setSessionVenues(snap.session_venues);
        setSessionArtists(snap.session_artists);
        setSessionReservation(snap.session_reservation);
        setSessionZeptoProducts(snap.session_zepto_products);
        setSessionZeptoAddresses(snap.session_zepto_addresses);
        setSessionZeptoCart(snap.session_zepto_cart);
        setSessionInstamartProducts(snap.session_instamart_products);
        setSessionInstamartAddresses(snap.session_instamart_addresses);
        setSessionInstamartCart(snap.session_instamart_cart);
        setSelectedInstamartAddressId(snap.selected_instamart_address_id ?? null);
        setSessionKiteInstruments(snap.session_kite_instruments);
        setSessionSerpFlights(snap.session_serp_flights ?? []);
        setSessionSerpProducts(snap.session_serp_products ?? []);
        setSessionSerpPlaces(snap.session_serp_places ?? []);
        setSessionSerpHotels(snap.session_serp_hotels ?? []);
        setSessionKiteHoldings(snap.session_kite_holdings);
        setSessionKitePositions(snap.session_kite_positions);
        setSessionKiteOpenOrders(snap.session_kite_open_orders);
        setSessionKiteOrderTicket(snap.session_kite_order_ticket);
      }
      await loadChatMessages(chatId, { silent: true });
    }
    try {
      const providersData = await api.get('/api/tools/providers/');
      setToolProviders(providersData.providers || []);
    } catch (err) {
      console.error('Failed to load initial data', err);
    }
    refreshAllMcpStatuses();
    refreshGcalStatus();
    refreshMapsStatus();
  };

  const refreshChatList = async () => {
    try {
      const data = await api.get('/api/chats/?limit=80');
      const list: ChatSummary[] = Array.isArray(data?.chats) ? data.chats : [];
      setChats(list);
      if (chatId && !list.some((c) => c.chat_id === chatId)) {
        clearChatSession(chatId);
        setChatId(null);
        localStorage.removeItem(ACTIVE_CHAT_STORAGE_KEY);
        setMessages([]);
      }
    } catch (err) {
      console.error('Failed to load chat list', err);
    }
  };

  const loadChatMessages = async (
    targetChatId: string,
    opts: { silent?: boolean } = {},
  ) => {
    try {
      const data = await api.get(
        `/api/chats/${encodeURIComponent(targetChatId)}/messages/?limit=80`,
      );
      const list = Array.isArray(data?.messages) ? data.messages : [];
      setMessages(
        list
          .filter((item: any) => item?.role === 'user' || item?.role === 'assistant')
          .map((item: any) => ({
            role: item.role,
            content: String(item.content || ''),
            ...(Array.isArray(item.meta) ? { meta: item.meta } : {}),
            ...(typeof item.provider === 'string' && item.provider ? { provider: item.provider } : {}),
            ...(typeof item.created_at === 'string' ? { created_at: item.created_at } : {}),
            ...(typeof item.message_id === 'string' ? { message_id: item.message_id } : {}),
            ...(Array.isArray(item.attachments) && item.attachments.length > 0
              ? { attachments: item.attachments }
              : {}),
          }))
          .filter((item: Message) => item.content.trim()),
      );
    } catch (err) {
      if (!opts.silent) {
        console.error('Failed to load chat messages', err);
      }
    }
  };

  const handleSelectChat = async (newChatId: string) => {
    if (newChatId === chatId) return;
    setChatId(newChatId);
    localStorage.setItem(ACTIVE_CHAT_STORAGE_KEY, newChatId);
    setIsAdminOpen(false);
    const snap = loadChatSession(newChatId);
    setSessionState(snap?.session_state ?? {});
    setSessionRestaurants(snap?.session_restaurants ?? []);
    setSessionAddresses(snap?.session_addresses ?? []);
    setSessionMenu(snap?.session_menu ?? []);
    setSessionCart(snap?.session_cart ?? null);
    setSessionMovies(snap?.session_movies ?? []);
    setSessionTheatres(snap?.session_theatres ?? []);
    setSessionEvents(snap?.session_events ?? []);
    setSessionVenues(snap?.session_venues ?? []);
    setSessionArtists(snap?.session_artists ?? []);
    setSessionReservation(snap?.session_reservation ?? null);
    setSessionZeptoProducts(snap?.session_zepto_products ?? []);
    setSessionZeptoAddresses(snap?.session_zepto_addresses ?? []);
    setSessionZeptoCart(snap?.session_zepto_cart ?? null);
    setSessionInstamartProducts(snap?.session_instamart_products ?? []);
    setSessionInstamartAddresses(snap?.session_instamart_addresses ?? []);
    setSessionInstamartCart(snap?.session_instamart_cart ?? null);
    setSelectedInstamartAddressId(snap?.selected_instamart_address_id ?? null);
    setSessionKiteInstruments(snap?.session_kite_instruments ?? []);
    setSessionSerpFlights(snap?.session_serp_flights ?? []);
    setSessionSerpProducts(snap?.session_serp_products ?? []);
    setSessionSerpPlaces(snap?.session_serp_places ?? []);
    setSessionSerpHotels(snap?.session_serp_hotels ?? []);
    setSessionKiteHoldings(snap?.session_kite_holdings ?? null);
    setSessionKitePositions(snap?.session_kite_positions ?? null);
    setSessionKiteOpenOrders(snap?.session_kite_open_orders ?? []);
    setSessionKiteOrderTicket(snap?.session_kite_order_ticket ?? null);
    setMessages([]);
    await loadChatMessages(newChatId);
  };

  const handleDeleteChat = async (targetChatId: string) => {
    try {
      await api.delete(`/api/chats/${encodeURIComponent(targetChatId)}/`);
    } catch (err) {
      console.error('Failed to delete chat', err);
      return;
    }
    setChats((prev) => prev.filter((c) => c.chat_id !== targetChatId));
    clearChatSession(targetChatId);
    if (targetChatId === chatId) {
      setChatId(null);
      localStorage.removeItem(ACTIVE_CHAT_STORAGE_KEY);
      setMessages([]);
      setSessionState({});
      setSessionRestaurants([]);
      setSessionAddresses([]);
      setSessionMenu([]);
      setSessionCart(null);
      setSessionMovies([]);
      setSessionTheatres([]);
      setSessionEvents([]);
      setSessionVenues([]);
      setSessionArtists([]);
      setSessionReservation(null);
      setSessionZeptoProducts([]);
      setSessionZeptoAddresses([]);
      setSessionZeptoCart(null);
      setSessionInstamartProducts([]);
      setSessionInstamartAddresses([]);
      setSessionInstamartCart(null);
      setSelectedInstamartAddressId(null);
      setSessionKiteInstruments([]);
      setSessionSerpFlights([]);
      setSessionSerpProducts([]);
      setSessionSerpPlaces([]);
      setSessionSerpHotels([]);
      setSessionKiteHoldings(null);
      setSessionKitePositions(null);
      setSessionKiteOpenOrders([]);
      setSessionKiteOrderTicket(null);
    }
  };

  const refreshAllMcpStatuses = async () => {
    setIsLoadingMcp(true);
    try {
      const data = await api.get('/api/mcp/status/all/');
      const list: McpStatus[] = Array.isArray(data?.statuses) ? data.statuses : [];
      const next: Record<string, McpStatus> = {};
      for (const entry of list) {
        const id = (entry as any).provider;
        if (typeof id === 'string' && id) next[id] = entry;
      }
      setMcpStatuses(next);
    } catch (err) {
      console.error('Failed to load MCP statuses', err);
    } finally {
      setIsLoadingMcp(false);
    }
  };

  const refreshGcalStatus = async () => {
    try {
      const data = await api.get('/api/google-calendar/status/');
      setGcalStatus(data);
    } catch (err) {
      console.error('Failed to check Google Calendar status', err);
    }
  };

  const refreshMapsStatus = async () => {
    try {
      setMapsConfigured(await mapsStatus());
    } catch (err) {
      console.error('Failed to check Google Maps status', err);
      setMapsConfigured(false);
    }
  };

  const handleLoginSuccess = (user: string) => {
    setUsername(user);
    setIsAuthenticated(true);
    loadInitialData();
  };

  const handleLogout = () => {
    clearToken();
    clearRefreshToken();
    setIsAuthenticated(false);
    setUsername('');
    setMessages([]);
    setSessionState({});
    setSessionRestaurants([]);
    setSessionAddresses([]);
    setSessionMenu([]);
    setSessionCart(null);
    setSessionMovies([]);
    setSessionTheatres([]);
    setSessionEvents([]);
    setSessionVenues([]);
    setSessionArtists([]);
    setSessionReservation(null);
    setSessionZeptoProducts([]);
    setSessionZeptoAddresses([]);
    setSessionZeptoCart(null);
    setSessionInstamartProducts([]);
    setSessionInstamartAddresses([]);
    setSessionInstamartCart(null);
    setSelectedInstamartAddressId(null);
    setSessionKiteInstruments([]);
    setSessionSerpFlights([]);
    setSessionSerpProducts([]);
    setSessionSerpPlaces([]);
    setSessionSerpHotels([]);
    setSessionKiteHoldings(null);
    setSessionKitePositions(null);
    setSessionKiteOpenOrders([]);
    setSessionKiteOrderTicket(null);
    setChats([]);
    setChatId(null);
    localStorage.removeItem(ACTIVE_CHAT_STORAGE_KEY);
    clearAllChatSessions();
  };

  const handleNewChat = () => {
    setIsAdminOpen(false);
    setMessages([]);
    agentChatIdRef.current = null;   // fresh agent conversation
    setSessionState({});
    setSessionRestaurants([]);
    setSessionAddresses([]);
    setSessionMenu([]);
    setSessionCart(null);
    setSessionMovies([]);
    setSessionTheatres([]);
    setSessionEvents([]);
    setSessionVenues([]);
    setSessionArtists([]);
    setSessionReservation(null);
    setSessionZeptoProducts([]);
    setSessionZeptoAddresses([]);
    setSessionZeptoCart(null);
    setSessionInstamartProducts([]);
    setSessionInstamartAddresses([]);
    setSessionInstamartCart(null);
    setSelectedInstamartAddressId(null);
    setSessionKiteInstruments([]);
    setSessionSerpFlights([]);
    setSessionSerpProducts([]);
    setSessionSerpPlaces([]);
    setSessionSerpHotels([]);
    setSessionKiteHoldings(null);
    setSessionKitePositions(null);
    setSessionKiteOpenOrders([]);
    setSessionKiteOrderTicket(null);
    setChatId(null);
    localStorage.removeItem(ACTIVE_CHAT_STORAGE_KEY);
  };

  // The request payload was deliberately reduced to the essentials while the
  // backend is rebuilt. The old turn shipped ~30 round-tripped `session_*`
  // echoes (cart/restaurants/menu/zepto/instamart/kite/serp/...), plus
  // `force_providers` and `selected_instamart_address_id`, so the legacy
  // orchestrator could rehydrate context. The new backend will define its own
  // context protocol — until then we send only the message + basic context.
  // New agent backend (/api/agent/turn): backend owns history/cart/session state, so the
  // frontend sends only message + mode + geo + chat_id. Taps go via `client_action` (below).
  // The address the user has selected (sidebar / cart "Change") is authoritative — send it every
  // turn so the agent operates on the right delivery context even when it changed outside the chat.
  const currentAddressIds = (): Record<string, string> | undefined => {
    const ids: Record<string, string> = {};
    if (selectedInstamartAddressId) ids.instamart = selectedInstamartAddressId;
    if (selectedZeptoAddressId) ids.zepto = selectedZeptoAddressId;
    return Object.keys(ids).length ? ids : undefined;
  };

  const buildAgentBody = (content: string, geo?: GeoCoordinate | null) => {
    const body: Record<string, any> = { message: content, mode: 'text' };
    if (geo) body.geo = { lat: geo.latitude, lng: geo.longitude };
    if (agentChatIdRef.current) body.chat_id = agentChatIdRef.current;
    const addressIds = currentAddressIds();
    if (addressIds) body.address_ids = addressIds;
    return body;
  };

  // Tool-heavy turns run past API Gateway's 30s cap, so the backend may
  // answer with {status:'accepted', job_id} instead of the final envelope.
  // This helper makes that transparent: it polls the job, surfaces each
  // rolling progress line into the thinking bubble, and resolves with the
  // final envelope exactly as if the POST had returned it directly.
  // Posts a turn and returns the response. The old async-job transport
  // (status:'accepted' + job_id polling on /api/agent/jobs/) was removed with
  // the legacy execution backend; the new backend will define its own.
  const runTurn = async (body: Record<string, any>): Promise<QueryResponse> => {
    return (await api.post('/api/agent/turn', body)) as QueryResponse;
  };

  const handleGrantLocation = async (geo: GeoCoordinate) => {
    const query = pendingLocationQuery;
    if (!query) return;
    setPendingLocationQuery(null);
    setGrantedGeo(geo);
    setMessages((prev) => prev.map((m, i) => i === prev.length - 1 ? { ...m, askLocation: false } : m));
    setIsThinking(true);
    try {
      const data: QueryResponse = await runTurn(buildAgentBody(query, geo));
const assistantMsg: Message = {
        role: 'assistant',
        content: data.assistant_message,
        meta: (Array.isArray(data.selected_tool_providers) ? data.selected_tool_providers : []) as string[],
        toolsOk: data.tool_execution?.ok !== false,
        toolsError: data.tool_execution?.ok === false ? data.tool_execution?.error : undefined,
        trace: data.observable_trace,
        attachments: Array.isArray(data.attachments) ? data.attachments : undefined,
        provider: typeof data.routed_provider === 'string' && data.routed_provider ? data.routed_provider : undefined,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err: any) {
      const msg = err?.reason === 'permission_denied'
        ? "Location access was denied. Please allow it in browser settings and try again."
        : "Couldn't get your location. Please try again.";
      setMessages((prev) => [...prev, { role: 'assistant', content: msg }]);
    } finally {
      setIsThinking(false);
    }
  };

  // Apply all session/sidebar state carried by a turn response (shared by streaming + non-streaming).
  // Pure side-effects on React state; never touches the chat messages.
  const applySessionState = (data: any) => {
    if (data.session_state && typeof data.session_state === 'object') setSessionState(data.session_state);
    if (Array.isArray(data.session_restaurants)) setSessionRestaurants(data.session_restaurants);
    if (Array.isArray(data.session_addresses)) setSessionAddresses(data.session_addresses);
    if (Array.isArray(data.session_menu)) setSessionMenu(data.session_menu);
    if ('session_cart' in data) {
      const c = (data as any).session_cart;
      setSessionCart(c && typeof c === 'object' && c.cart_id ? c : null);
      if (c && typeof c === 'object' && c.cart_id
          && (data.routed_provider === 'zomato' || data.routed_provider === 'swiggy_food')) {
        setFoodCartProvider(data.routed_provider);
      }
    }
    if (Array.isArray(data.session_movies)) setSessionMovies(data.session_movies);
    if (Array.isArray(data.session_theatres)) setSessionTheatres(data.session_theatres);
    if (Array.isArray(data.session_events)) setSessionEvents(data.session_events);
    if (Array.isArray(data.session_venues)) setSessionVenues(data.session_venues);
    if (Array.isArray(data.session_artists)) setSessionArtists(data.session_artists);
    if ('session_reservation' in data) {
      const r = (data as any).session_reservation;
      setSessionReservation(r && typeof r === 'object' && r.booking_id ? r : null);
    }
    if (Array.isArray(data.session_zepto_products)) setSessionZeptoProducts(data.session_zepto_products);
    if (Array.isArray(data.session_zepto_addresses)) setSessionZeptoAddresses(data.session_zepto_addresses);
    if ('session_zepto_cart' in data) {
      const zc = (data as any).session_zepto_cart;
      setSessionZeptoCart(zc && typeof zc === 'object' ? zc : null);
    }
    if (Array.isArray(data.session_instamart_products)) setSessionInstamartProducts(data.session_instamart_products);
    if (Array.isArray(data.session_instamart_addresses)) setSessionInstamartAddresses(data.session_instamart_addresses);
    if ('session_instamart_cart' in data) {
      const ic = (data as any).session_instamart_cart;
      setSessionInstamartCart(ic && typeof ic === 'object' ? ic : null);
    }
    if ('selected_instamart_address_id' in data) {
      const sid = (data as any).selected_instamart_address_id;
      setSelectedInstamartAddressId(typeof sid === 'string' && sid ? sid : null);
      // Seed the selected address (with the backend-resolved label) into the saved-address list so
      // the "Delivering to" bar shows the real address at first paint, before the full list loads.
      // The async list fetch replaces this with the complete set when it arrives.
      const label = (data as any).selected_instamart_address_label;
      if (sid && typeof label === 'string' && label) {
        setInstamartSavedAddresses((prev) =>
          prev.some((a) => a.address_id === sid)
            ? prev
            : [{ address_id: sid, location_name: label.replace(' · ', ' — ') }, ...prev],
        );
      }
    }
    if (Array.isArray(data.session_zepto_addresses) && data.session_zepto_addresses.length) {
      setZeptoSavedAddresses(
        (data.session_zepto_addresses as any[]).map((r) => ({
          address_id: String(r.address_id || ''),
          location_name:
            r.tag && r.line ? `${r.tag} — ${r.line}` : String(r.label || r.line || r.tag || r.address_id || ''),
        })).filter((r) => r.address_id),
      );
    }
    if ('selected_zepto_address_id' in data) {
      const zid = (data as any).selected_zepto_address_id;
      setSelectedZeptoAddressId(typeof zid === 'string' && zid ? zid : null);
      // Seed the selected Zepto address (+ backend label) so the bar shows it at first paint.
      const zlabel = (data as any).selected_zepto_address_label;
      if (zid && typeof zlabel === 'string' && zlabel) {
        setZeptoSavedAddresses((prev) =>
          prev.some((a) => a.address_id === zid)
            ? prev
            : [{ address_id: zid, location_name: zlabel.replace(' · ', ' — ') }, ...prev],
        );
      }
    }
    if (Array.isArray(data.session_kite_instruments)) setSessionKiteInstruments(data.session_kite_instruments);
    if (Array.isArray(data.session_serp_flights)) setSessionSerpFlights(data.session_serp_flights);
    if (Array.isArray(data.session_serp_products)) setSessionSerpProducts(data.session_serp_products);
    if (Array.isArray(data.session_serp_places)) setSessionSerpPlaces(data.session_serp_places);
    if (Array.isArray(data.session_serp_hotels)) setSessionSerpHotels(data.session_serp_hotels);
    if ('session_kite_holdings' in data) {
      const kh = (data as any).session_kite_holdings;
      setSessionKiteHoldings(kh && typeof kh === 'object' ? kh : null);
    }
    if ('session_kite_positions' in data) {
      const kp = (data as any).session_kite_positions;
      setSessionKitePositions(kp && typeof kp === 'object' ? kp : null);
    }
    if (Array.isArray(data.session_kite_open_orders)) setSessionKiteOpenOrders(data.session_kite_open_orders);
    if ('session_kite_order_ticket' in data) {
      const kt = (data as any).session_kite_order_ticket;
      setSessionKiteOrderTicket(kt && typeof kt === 'object' ? kt : null);
    }
  };

  // Build the assistant message from a full turn response + append it (non-streaming / tap path).
  const finalizeAssistant = (data: any) => {
    if ((data as any).chat_id) agentChatIdRef.current = (data as any).chat_id;
    applySessionState(data);
    setMessages((prev) => [...prev, {
      role: 'assistant',
      content: data.assistant_message,
      attachments: Array.isArray(data.attachments) ? data.attachments : undefined,
      provider: typeof data.routed_provider === 'string' && data.routed_provider ? data.routed_provider : undefined,
      agentCards: Array.isArray((data as any).cards) ? (data as any).cards : undefined,
      pendingAction: (data as any).pending_action ?? null,
    } as Message]);
  };

  // Stream a turn over SSE: one assistant message plays out as the backend narrates — text lines
  // type in, each tool's cards build in (completion order). Falls back to the plain endpoint if the
  // stream can't open (e.g. behind API Gateway). The FE imposes NO ordering — it renders as it arrives.
  const streamSend = async (sendBody: Record<string, any>): Promise<string> => {
    const sid = `sm-${Date.now()}`;
    let added = false;
    let sawCards = false;
    let finalText = '';
    const ensure = () => {
      if (!added) {
        added = true;
        // The stream is now producing the actual message (with its own typing dots) → retire the
        // pre-stream "thinking" indicator so the two don't show at once.
        setIsThinking(false);
        setMessages((p) => [...p, { role: 'assistant', message_id: sid, content: '', segments: [], streaming: true } as Message]);
      }
    };
    const pushSeg = (seg: StreamSegment) =>
      setMessages((p) => p.map((m) => (m.message_id === sid ? { ...m, segments: [...(m.segments || []), seg] } : m)));
    const patch = (u: Partial<Message>) =>
      setMessages((p) => p.map((m) => (m.message_id === sid ? { ...m, ...u } : m)));
    const finalize = (data: any) => {
      if ((data as any).chat_id) agentChatIdRef.current = (data as any).chat_id;
      finalText = data.assistant_message || '';
      applySessionState(data);
      // If the turn produced cards but never streamed a `cards` event (e.g. a cart op), show them now.
      if (!sawCards && Array.isArray(data.attachments) && data.attachments.length) {
        pushSeg({ kind: 'cards', attachments: data.attachments });
      }
      patch({
        content: data.assistant_message || '',
        attachments: Array.isArray(data.attachments) ? data.attachments : undefined,
        agentCards: Array.isArray((data as any).cards) ? (data as any).cards : undefined,
        pendingAction: (data as any).pending_action ?? null,
        streaming: false,
      });
    };
    try {
      await streamPost('/api/agent/turn/stream', sendBody, (ev: any) => {
        switch (ev.type) {
          case 'status': ensure(); patch({ working: { action: ev.action, provider: ev.provider } }); break;
          case 'say': ensure(); if ((ev.text || '').trim()) pushSeg({ kind: 'text', text: ev.text }); break;
          case 'cards': ensure(); sawCards = true; if (Array.isArray(ev.attachments) && ev.attachments.length) pushSeg({ kind: 'cards', attachments: ev.attachments, provider: ev.provider }); break;
          case 'done': ensure(); finalize(ev.result || {}); break;
          case 'error': ensure(); pushSeg({ kind: 'text', text: `Error: ${ev.message || 'kuch dikkat aa gayi'}` }); patch({ streaming: false }); break;
        }
      });
    } catch {
      // SSE unavailable → fall back to the plain endpoint (no live typing, same content).
      const data = await runTurn(sendBody);
      if (added) finalize(data);
      else finalizeAssistant(data);
      finalText = data.assistant_message || finalText;
    }
    return finalText;
  };

  const handleSendMessage = async (
    content: string,
    geoOverride?: import('./types').GeoCoordinate | null,
  ): Promise<string> => {
    const text = (content || '').trim();
    if (!text) return '';
    // Card taps arrive as "@@A@@<json>" → a deterministic client_action (no user bubble, no LLM).
    const isAction = text.startsWith('@@A@@');
    const clientAction = isAction
      ? (() => { try { return JSON.parse(text.slice(5)); } catch { return null; } })()
      : null;
    if (!isAction) {
      setMessages((prev) => [...prev, { role: 'user', content: text } as Message]);
    } else if (clientAction?.type === 'add' && clientAction?.payload?.item_id) {
      // A deterministic "add this card" tap — show a clean user bubble with just the item name
      // (the exact id is carried in the tap payload, not shown to the user).
      const p = clientAction.payload;
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: `➕ ${p.name || 'item'}` } as Message,
      ]);
    } else if (
      (clientAction?.type === 'set_qty' || clientAction?.type === 'remove') &&
      clientAction?.payload?.item_id
    ) {
      // A deterministic cart qty change / remove tap (the cart "+"/"−" controls) — clean bubble with
      // just the item + new qty; the exact id rides in the payload, never shown.
      const p = clientAction.payload;
      const name = p.name || 'item';
      const content =
        clientAction.type === 'remove' || p.qty === 0
          ? `🗑️ ${name}`
          : `🔄 ${name} × ${p.qty}`;
      setMessages((prev) => [...prev, { role: 'user', content } as Message]);
    }
    setIsThinking(true);

    try {
      const sendBody = isAction
        ? {
            mode: 'text',
            client_action: clientAction,
            ...(agentChatIdRef.current ? { chat_id: agentChatIdRef.current } : {}),
            ...(currentAddressIds() ? { address_ids: currentAddressIds() } : {}),
          }
        : buildAgentBody(text, geoOverride ?? effectiveGeo);
      // Everything streams the SAME way — taps (add / qty change) and NL alike — so the response
      // always types out and builds up live (opener → narration → cards), never appearing all at
      // once. streamSend falls back to the plain endpoint internally if SSE is unavailable.
      return await streamSend(sendBody);
    } catch (err: any) {
      const errorMsg: Message = {
        role: 'assistant',
        content: `Error: ${err.message || 'Something went wrong'}`,
      };
      setMessages((prev) => [...prev, errorMsg]);
      return '';
    } finally {
      setIsThinking(false);
    }
  };

  // Groceries quick-action (issue #59): instead of firing a fixed search,
  // drop an in-chat category picker into the conversation. Categories have no
  // backing MCP tool (both providers are search-only), so this is a purely
  // client-side turn — a "user" bubble + a cards-only assistant turn carrying
  // the `grocery_categories` attachment. Tapping a tile sends a real search
  // (Instamart + Zepto fan-out) through handleSendMessage like any other.
  const handleOpenGroceries = () => {
    if (isThinking) return;
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: 'Order groceries' },
      { role: 'assistant', content: '', attachments: [{ kind: 'grocery_intro' }] },
      { role: 'assistant', content: '', attachments: [{ kind: 'grocery_categories' }] },
    ]);
  };

  // Order Food quick-action: same client-side pattern as groceries — drop an
  // in-chat cuisine picker (a "user" bubble + a cards-only assistant turn with
  // the `food_categories` attachment). Tapping a tile fires a real Zomato /
  // Swiggy Food search through handleSendMessage.
  const handleOpenFood = () => {
    if (isThinking) return;
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: 'Order food' },
      { role: 'assistant', content: '', attachments: [{ kind: 'food_intro' }] },
      { role: 'assistant', content: '', attachments: [{ kind: 'food_categories' }] },
    ]);
  };

  // Movies & Events quick-action: same client-side pattern — drop an in-chat
  // District discovery picker (a "user" bubble + a cards-only assistant turn
  // with the `district_categories` attachment). Tapping a tile fires a real,
  // location-scoped District search through handleSendMessage.
  const handleOpenDistrict = () => {
    if (isThinking) return;
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: "What's on near me" },
      { role: 'assistant', content: '', attachments: [{ kind: 'district_categories' }] },
    ]);
  };

  const handleVoiceError = (message: string) => {
    console.error('[Voice] Error:', message);
  };

  const openSettings = (tab: 'mcp' | 'calendar' | 'models' = 'mcp', providerId?: string) => {
    setSettingsInitialTab(tab);
    setSettingsInitialProvider(providerId);
    setIsSettingsOpen(true);
  };

  // Returning from a hosted MCP OAuth round trip: the callback page sends the
  // browser back to /?mcp_oauth=success&provider=<id>. Reopen Settings on that
  // provider so its freshly-vaulted status is visible, then clean the URL.
  useEffect(() => {
    if (!isAuthenticated) return;
    const params = new URLSearchParams(window.location.search);
    if (params.get('mcp_oauth') !== 'success') return;
    openSettings('mcp', params.get('provider') || undefined);
    window.history.replaceState({}, '', window.location.pathname);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated]);

  // Path-based chat routing. One effect mirrors `chatId` → `/c/<id>` so the URL
  // always names the open chat; a `popstate` listener mirrors the other way for
  // back/forward. The path-equality guard makes the two non-circular: a
  // popstate-driven `setChatId` lands on a URL that already matches, so the sync
  // effect pushes nothing back.
  const navRef = useRef({ select: handleSelectChat, neu: handleNewChat, chatId });
  navRef.current = { select: handleSelectChat, neu: handleNewChat, chatId };
  const urlSyncedRef = useRef(false);
  useEffect(() => {
    const desired = chatPathFor(chatId);
    if (window.location.pathname !== desired) {
      // First sync replaces (no spurious history entry on load); later chat
      // switches push so the back button walks the visited chats.
      if (urlSyncedRef.current) window.history.pushState(null, '', desired);
      else window.history.replaceState(null, '', desired);
    }
    urlSyncedRef.current = true;
  }, [chatId]);
  useEffect(() => {
    const onPopState = () => {
      const target = chatIdFromPath();
      if (target === navRef.current.chatId) return;
      if (target) navRef.current.select(target);
      else navRef.current.neu();
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  // Load the Swiggy saved-address list once Instamart is connected (same
  // endpoint the Apps modal uses) so the delivery-address bar can map the
  // selected address id → human label. The product-card attachment only
  // carries the opaque id; without this list the bar shows a placeholder.
  const instamartConnected = useMemo(() => {
    const st = (mcpStatuses['swiggy_instamart'] as any)?.status;
    return st === 'ready' || st === 'ready_pending';
  }, [mcpStatuses]);
  useEffect(() => {
    if (!isAuthenticated || !instamartConnected) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get('/api/food/saved-addresses/?provider=swiggy_instamart');
        if (!cancelled && Array.isArray(data?.addresses)) {
          setInstamartSavedAddresses(data.addresses as InstamartSavedAddress[]);
        }
      } catch (err) {
        console.error('load instamart saved addresses failed', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, instamartConnected]);

  // Load the Zepto saved-address list once Zepto is connected (same endpoint the
  // Apps modal uses) so the "Delivering to" bar can map the selected id → label.
  const zeptoConnected = useMemo(() => {
    const st = (mcpStatuses['zepto'] as any)?.status;
    return st === 'ready' || st === 'ready_pending';
  }, [mcpStatuses]);
  useEffect(() => {
    if (!isAuthenticated || !zeptoConnected) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get('/api/food/saved-addresses/?provider=zepto');
        if (!cancelled && Array.isArray(data?.addresses)) {
          setZeptoSavedAddresses(data.addresses as ZeptoSavedAddress[]);
        }
      } catch (err) {
        console.error('load zepto saved addresses failed', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, zeptoConnected]);

  // Cosmetic orders handlers — local-only state (no backend wiring yet).
  const handleCancelOrder = (id: string) =>
    setOrders((prev) =>
      prev.map((o) =>
        o.id === id
          ? { ...o, status: o.status === 'Cancelled' ? 'Preparing' : 'Cancelled' }
          : o,
      ),
    );
  const handleAddOrder = (provider: string, text: string, category: 'daily' | 'today', price: string) => {
    setOrders((prev) => [
      ...prev,
      {
        id: `ord-${Date.now()}`,
        category,
        provider,
        text,
        status: category === 'daily' ? 'Active Subscription' : 'Preparing',
        time: category === 'daily' ? 'Daily 10 AM' : 'ETA 12 mins',
        price: price || '₹150',
      },
    ]);
  };
  const handleDeleteOrder = (id: string) =>
    setOrders((prev) => prev.filter((o) => o.id !== id));


  const isAdmin = ADMIN_USERNAMES.has(username);

  if (loading) {
    return (
      <div className="h-[var(--app-height)] flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-slate-900 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Login onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <InstamartAddressProvider
      value={{
        addresses: instamartSavedAddresses,
        selectedId: selectedInstamartAddressId,
        onSwitch: (addressId, prompt) => {
          if (isThinking || !addressId) return;
          // Commit optimistically so the bar reflects the switch immediately.
          // (The turn no longer round-trips the selected address to the backend;
          // the new backend will define how address context is supplied.)
          setSelectedInstamartAddressId(addressId);
          handleSendMessage(prompt);
        },
      }}
    >
    <ZeptoAddressProvider value={{ addresses: zeptoSavedAddresses, selectedId: selectedZeptoAddressId }}>
    <McpProviderProvider
      value={{
        statuses: mcpStatuses,
        isConnected: (pid) => {
          const st = (mcpStatuses[pid] as any)?.status;
          return st === 'ready' || st === 'ready_pending';
        },
        connect: (pid) => {
          setAppsInitialProvider(pid);
          setShowApps(true);
        },
      }}
    >
    <div className="flex h-[var(--app-height)] w-screen overflow-hidden font-sans select-none relative pt-[var(--safe-top)] box-border">
      <LeftSidebar
        username={username}
        isAdmin={isAdmin}
        onLogout={handleLogout}
        onOpenAdmin={() => {
          setIsAdminOpen(true);
        }}
        onOpenSettings={() => openSettings('mcp')}
        onNewChat={() => {
          handleNewChat();
          closeSidebarOnMobile();
        }}
        chats={chats}
        activeChatId={chatId}
        onSelectChat={(id) => {
          handleSelectChat(id);
          closeSidebarOnMobile();
        }}
        onDeleteChat={handleDeleteChat}
        toolProviders={toolProviders}
        mcpStatuses={mcpStatuses}
        onSelectProvider={(id) => openSettings('mcp', id)}
        onOpenApps={() => setShowApps(true)}
        appsOpen={showApps}
        isCollapsed={isSidebarCollapsed}
        onToggleCollapse={() => setIsSidebarCollapsed((v) => !v)}
      />

      <div className="flex-1 flex flex-col relative overflow-hidden min-w-0">
        {isAdminOpen ? (
          <AdminDashboard onClose={() => setIsAdminOpen(false)} />
        ) : (
          <ChatArea
            messages={messages}
            onSendMessage={handleSendMessage}
            isThinking={isThinking}
            onSuggestionClick={(prompt) => {
              if (!isThinking) handleSendMessage(prompt);
            }}
            onOpenGroceries={handleOpenGroceries}
            onOpenFood={handleOpenFood}
            onOpenDistrict={handleOpenDistrict}
            onOpenApps={() => setShowApps(true)}
            onToggleLeft={() => setIsSidebarCollapsed((v) => !v)}
            isSidebarCollapsed={isSidebarCollapsed}
            connectedCount={
              Object.values(mcpStatuses).filter((s) => {
                const st = (s as any)?.status;
                return st === 'ready' || st === 'ready_pending';
              }).length
            }
            username={username}
            sessionAddresses={sessionAddresses}
            onSelectAddress={(addr) => {
              const label = (addr.location_name || addr.address_id || '').trim();
              if (!label || isThinking) return;
              handleSendMessage(`Switch my delivery address to: ${label}.`);
            }}
            onQuickPrompt={(prompt) => {
              if (!isThinking) handleSendMessage(prompt);
            }}
            quickPromptDisabled={isThinking}
            onGrantLocation={handleGrantLocation}
            onVoiceError={handleVoiceError}
            selectedAddressId={selectedAddressId ?? sessionState['__ADDRESS_ID__'] ?? null}
            onPickAddress={(addr) => {
              const id = String(addr.address_id || '').trim();
              if (!id) return;
              setSelectedAddressId(id);
              setSessionState((prev) => ({ ...prev, __ADDRESS_ID__: id }));
            }}
          />
        )}

        {!isAdminOpen && (
          <CartDock
            foodCart={sessionCart}
            foodCartProvider={foodCartProvider}
            zeptoCart={sessionZeptoCart}
            instamartCart={sessionInstamartCart}
            onQuickPrompt={(prompt) => {
              if (!isThinking) handleSendMessage(prompt);
            }}
            disabled={isThinking}
          />
        )}
      </div>

      <AppsModal
        isOpen={showApps}
        initialProvider={appsInitialProvider}
        onClose={() => {
          setShowApps(false);
          setAppsInitialProvider(null);
          // A connect/disconnect may have happened inside — refresh so the
          // grocery "Search in" chips reflect the new status immediately.
          refreshAllMcpStatuses();
        }}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        toolProviders={toolProviders}
        mcpStatuses={mcpStatuses}
        onMcpStatusesChange={setMcpStatuses}
        isLoadingMcp={isLoadingMcp}
        onLoadingMcpChange={setIsLoadingMcp}
        gcalStatus={gcalStatus}
        onGcalStatusChange={setGcalStatus}
        mapsConfigured={mapsConfigured}
        onMapsConfiguredChange={setMapsConfigured}
        initialTab={settingsInitialTab}
        initialProviderId={settingsInitialProvider}
      />

      <AnimatePresence>
      </AnimatePresence>

      <AnimatePresence>
        {showOrdersPopup && (
          <OrdersPopup
            isOpen={showOrdersPopup}
            onClose={() => setShowOrdersPopup(false)}
            orders={orders}
            onCancelOrder={handleCancelOrder}
            onAddOrder={handleAddOrder}
            onDeleteOrder={handleDeleteOrder}
          />
        )}
      </AnimatePresence>
    </div>
    </McpProviderProvider>
    </ZeptoAddressProvider>
    </InstamartAddressProvider>
  );
}

export default App;
