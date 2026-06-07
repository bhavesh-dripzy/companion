import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence } from 'motion/react';
import { Login } from './Login';
import { SettingsModal } from './SettingsModal';
import { AdminDashboard } from './AdminDashboard';
import { LeftSidebar } from './components/LeftSidebar';
import { ChatArea } from './components/ChatArea';
import { RemindersPopup } from './components/RemindersPopup';
import { OrdersPopup } from './components/OrdersPopup';
import { api, clearToken, getToken } from './api';
import type {
  Attachment,
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
  SessionKiteOpenOrders,
  SessionKiteOrderTicket,
  SessionKitePositions,
  ToolProvider,
  VoiceSessionState,
  VoiceToolCallRequest,
  VoiceToolCallResponse,
} from './types';
import { useUserLocation } from './useUserLocation';
import { mapsStatus } from './maps';
import './index.css';

// localStorage key holding the chat_id the user was last viewing. We
// restore it on reload so the workspace lands back in the same
// conversation; it gets cleared by "new chat" / logout, and overwritten
// every time the backend confirms (or lazy-creates) a chat_id.
const ACTIVE_CHAT_STORAGE_KEY = 'companion.active_chat_id';

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
  // Kite (Zerodha trading) session memory — instrument LRU, the latest
  // holdings / positions snapshots, the open-orders list, and the pending
  // unconfirmed order ticket. Same persistence contract as the other
  // indexes: stored locally so a reload restores the planner's context.
  session_kite_instruments: SessionKiteInstrument[];
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
      session_kite_instruments: Array.isArray(parsed.session_kite_instruments)
        ? (parsed.session_kite_instruments as SessionKiteInstrument[])
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

// Cosmetic, local-only stub for the Reminders popup that ships with
// the new UI shell. There's no backend route for reminders today, so
// these live entirely in component state. Pre-populated so the screen
// has something to render the first time the user opens it.
const INITIAL_REMINDERS = [
  { id: 'rem-d1', category: 'daily', text: 'Take daily vitamins & morning supplements', time: '09:00 AM', completed: false },
  { id: 'rem-d2', category: 'daily', text: 'Postural posture stretch & hydration check', time: 'Every 2 hrs', completed: false },
  { id: 'rem-d3', category: 'daily', text: 'Review evening workout session and wellness log', time: '06:30 PM', completed: true },
  { id: 'rem-t1', category: 'today', text: 'Confirm meeting with financial advisor', time: '02:00 PM', completed: false },
  { id: 'rem-t2', category: 'today', text: 'Track grocery restock through Blinkit/Zepto', time: '04:00 PM', completed: false },
  { id: 'rem-t3', category: 'today', text: 'Submit car registration insurance renewals', time: 'Today', completed: false },
];

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
  // header to bring it back.
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);

  // Cosmetic popups (no backend wiring) — kept local-only as confirmed
  // up-front. Users can toggle items / add / delete entries and the
  // state lives for the session.
  const [showRemindersPopup, setShowRemindersPopup] = useState(false);
  const [showOrdersPopup, setShowOrdersPopup] = useState(false);
  const [reminders, setReminders] = useState(INITIAL_REMINDERS);
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
  const [_voiceSessionState, setVoiceSessionState] = useState<VoiceSessionState>('inactive');
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
  // Kite (Zerodha trading) session memory. Same echo contract as the other
  // indexes — round-trip on every /api/agent/query/ so the planner can
  // re-use literal instrument triples, read the latest portfolio snapshots,
  // and resolve a confirmation against the pending order ticket.
  const [sessionKiteInstruments, setSessionKiteInstruments] = useState<SessionKiteInstrument[]>([]);
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
    () => localStorage.getItem(ACTIVE_CHAT_STORAGE_KEY) || null,
  );

  const { location: userLocation, ensureLocation } = useUserLocation();

  useEffect(() => {
    const checkAuth = async () => {
      if (!getToken()) {
        setLoading(false);
        return;
      }
      try {
        const data = await api.get('/api/auth/status/');
        setUsername(data.username);
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
      session_kite_instruments: sessionKiteInstruments,
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
    sessionKiteInstruments,
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
        setSessionKiteInstruments(snap.session_kite_instruments);
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
    setSessionKiteInstruments(snap?.session_kite_instruments ?? []);
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
      setSessionKiteInstruments([]);
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
    setSessionKiteInstruments([]);
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
    setSessionKiteInstruments([]);
    setSessionKiteHoldings(null);
    setSessionKitePositions(null);
    setSessionKiteOpenOrders([]);
    setSessionKiteOrderTicket(null);
    setChatId(null);
    localStorage.removeItem(ACTIVE_CHAT_STORAGE_KEY);
  };

  const buildAgentBody = (content: string, geo?: GeoCoordinate | null) => {
    const body: Record<string, any> = {
      message: content,
      address_id: '',
      execute_tools: true,
      history: messages.map((m) => ({
        role: m.role,
        content: m.content,
        ...(m.role === 'assistant' && m.provider ? { provider: m.provider } : {}),
      })),
    };
    if (geo) {
      body.geo = {
        latitude: geo.latitude,
        longitude: geo.longitude,
        ...(geo.accuracyMeters !== undefined ? { accuracy_meters: geo.accuracyMeters } : {}),
        captured_at: new Date().toISOString(),
      };
    }
    if (Object.keys(sessionState).length > 0) body.session_state = sessionState;
    if (sessionRestaurants.length > 0) body.session_restaurants = sessionRestaurants;
    if (sessionAddresses.length > 0) body.session_addresses = sessionAddresses;
    if (sessionMenu.length > 0) body.session_menu = sessionMenu;
    if (sessionCart && sessionCart.cart_id) body.session_cart = sessionCart;
    if (sessionMovies.length > 0) body.session_movies = sessionMovies;
    if (sessionTheatres.length > 0) body.session_theatres = sessionTheatres;
    if (sessionEvents.length > 0) body.session_events = sessionEvents;
    if (sessionVenues.length > 0) body.session_venues = sessionVenues;
    if (sessionArtists.length > 0) body.session_artists = sessionArtists;
    if (sessionReservation && sessionReservation.booking_id) {
      body.session_reservation = sessionReservation;
    }
    if (sessionZeptoProducts.length > 0) body.session_zepto_products = sessionZeptoProducts;
    if (sessionZeptoAddresses.length > 0) body.session_zepto_addresses = sessionZeptoAddresses;
    if (sessionZeptoCart) body.session_zepto_cart = sessionZeptoCart;
    if (sessionInstamartProducts.length > 0) body.session_instamart_products = sessionInstamartProducts;
    if (sessionInstamartAddresses.length > 0) body.session_instamart_addresses = sessionInstamartAddresses;
    if (sessionInstamartCart) body.session_instamart_cart = sessionInstamartCart;
    if (sessionKiteInstruments.length > 0) body.session_kite_instruments = sessionKiteInstruments;
    if (sessionKiteHoldings) body.session_kite_holdings = sessionKiteHoldings;
    if (sessionKitePositions) body.session_kite_positions = sessionKitePositions;
    if (sessionKiteOpenOrders.length > 0) body.session_kite_open_orders = sessionKiteOpenOrders;
    if (sessionKiteOrderTicket) body.session_kite_order_ticket = sessionKiteOrderTicket;
    if (chatId) body.chat_id = chatId;
    return body;
  };

  const handleSendMessage = async (content: string) => {
    const text = (content || '').trim();
    if (!text) return;
    const userMsg: Message = { role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    setIsThinking(true);

    try {
      let data: QueryResponse = await api.post('/api/agent/query/', buildAgentBody(text, userLocation));

      const errorCode = (data as any)?.tool_execution?.error_code;
      if (errorCode === 'needs_location') {
        try {
          const fresh = await ensureLocation();
          data = await api.post('/api/agent/query/', buildAgentBody(text, fresh));
        } catch (geoErr: any) {
          const reason = geoErr?.reason ?? 'unknown';
          const friendly: Record<string, string> = {
            permission_denied:
              "I couldn't get your location — you blocked the request. Allow location access in your browser and try again.",
            position_unavailable: 'Your location is currently unavailable. Try again with a better signal.',
            timeout: 'Getting your location timed out — please try again.',
            unsupported: 'This browser does not expose location services.',
            unknown: 'I could not read your location.',
          };
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', content: friendly[reason] || friendly.unknown },
          ]);
          return;
        }
      }

      const assistantMsg: Message = {
        role: 'assistant',
        content: data.assistant_message,
        meta: [
          ...data.selected_tool_providers,
          data.tool_execution.ok ? 'tools ok' : data.tool_execution.error,
        ].filter(Boolean) as string[],
        trace: data.observable_trace,
        attachments: Array.isArray(data.attachments) ? data.attachments : undefined,
        provider:
          typeof data.routed_provider === 'string' && data.routed_provider ? data.routed_provider : undefined,
      };

      if (data.session_state && typeof data.session_state === 'object') setSessionState(data.session_state);
      if (Array.isArray(data.session_restaurants)) setSessionRestaurants(data.session_restaurants);
      if (Array.isArray(data.session_addresses)) setSessionAddresses(data.session_addresses);
      if (Array.isArray(data.session_menu)) setSessionMenu(data.session_menu);
      if ('session_cart' in data) {
        const c = (data as any).session_cart;
        setSessionCart(c && typeof c === 'object' && c.cart_id ? c : null);
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
      if (Array.isArray(data.session_kite_instruments)) setSessionKiteInstruments(data.session_kite_instruments);
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

      if (typeof data.chat_id === 'string' && data.chat_id) {
        if (data.chat_id !== chatId) {
          setChatId(data.chat_id);
          localStorage.setItem(ACTIVE_CHAT_STORAGE_KEY, data.chat_id);
        }
        refreshChatList();
      }

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err: any) {
      const errorMsg: Message = {
        role: 'assistant',
        content: `Error: ${err.message || 'Something went wrong'}`,
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsThinking(false);
    }
  };

  // Cards (menu/restaurant/cart) returned by the most recent voice tool call,
  // held until that turn completes so we can attach them to the rendered
  // assistant message in the chat log.
  const pendingVoiceAttachmentsRef = useRef<Attachment[] | null>(null);

  const handleVoiceTurn = (userText: string, assistantText: string) => {
    // OpenAI path only (batch at disconnect). The Gemini path renders each
    // turn live via handleVoiceTurnComplete.
    const userMsg: Message = { role: 'user', content: userText };
    const assistantMsg: Message = { role: 'assistant', content: assistantText };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
  };

  const handleVoiceTurnComplete = async (
    userText: string,
    assistantText: string,
    hadToolCall: boolean,
  ) => {
    // Render this turn in the chat log, attaching any cards from the tool call.
    const attachments = hadToolCall ? pendingVoiceAttachmentsRef.current ?? undefined : undefined;
    pendingVoiceAttachmentsRef.current = null;
    setMessages((prev) => {
      const next = [...prev];
      if (userText) next.push({ role: 'user', content: userText });
      if (assistantText || attachments) {
        next.push({ role: 'assistant', content: assistantText, attachments });
      }
      return next;
    });

    // Tool turns are already persisted + leadgen-enqueued server-side by
    // /api/voice/tool-execute/. Persist conversational turns here so voice
    // history and the leadgen pipeline stay complete.
    if (!hadToolCall && (userText || assistantText)) {
      try {
        const data = await api.post('/api/voice/log-turn/', {
          user_text: userText,
          assistant_text: assistantText,
          chat_id: chatId,
        });
        if (data?.chat_id && data.chat_id !== chatId) {
          setChatId(data.chat_id);
          localStorage.setItem(ACTIVE_CHAT_STORAGE_KEY, data.chat_id);
        }
      } catch (err) {
        console.error('[Voice] log-turn failed', err);
      }
    }
    refreshChatList();
  };

  const handleVoiceToolCall = async (intent: string): Promise<string> => {
    const body: VoiceToolCallRequest = {
      intent,
      history: messages.map((m) => ({
        role: m.role,
        content: m.content,
      })),
      session_state: Object.keys(sessionState).length > 0 ? sessionState : undefined,
      chat_id: chatId,
      ...(sessionRestaurants.length > 0 ? { session_restaurants: sessionRestaurants } : {}),
      ...(sessionAddresses.length > 0 ? { session_addresses: sessionAddresses } : {}),
      ...(sessionCart ? { session_cart: sessionCart } : {}),
    };

    try {
      const data: VoiceToolCallResponse = await api.post('/api/voice/tool-execute/', body);
      if (data.success) {
        if (data.session_state && Object.keys(data.session_state).length > 0) {
          setSessionState(data.session_state);
        }
        if (data.session_cart !== undefined) {
          setSessionCart(data.session_cart && data.session_cart.cart_id ? data.session_cart : null);
        }
        if (data.session_restaurants && data.session_restaurants.length > 0) {
          setSessionRestaurants(data.session_restaurants);
        }
        if (Array.isArray(data.session_menu) && data.session_menu.length > 0) {
          setSessionMenu(data.session_menu);
        }
        // Stash cards for handleVoiceTurnComplete to render on this turn.
        pendingVoiceAttachmentsRef.current =
          Array.isArray(data.attachments) && data.attachments.length > 0 ? data.attachments : null;
        if (data.chat_id && data.chat_id !== chatId) {
          setChatId(data.chat_id);
          localStorage.setItem(ACTIVE_CHAT_STORAGE_KEY, data.chat_id);
        }
        return data.result;
      }
      return `I couldn't complete that request: ${data.error || 'Unknown error'}`;
    } catch (err: any) {
      return `Something went wrong: ${err.message || 'Unknown error'}`;
    }
  };

  const handleVoiceStateChange = (state: VoiceSessionState) => {
    setVoiceSessionState(state);
  };

  const handleVoiceError = (message: string) => {
    console.error('[Voice] Error:', message);
  };

  const openSettings = (tab: 'mcp' | 'calendar' | 'models' = 'mcp', providerId?: string) => {
    setSettingsInitialTab(tab);
    setSettingsInitialProvider(providerId);
    setIsSettingsOpen(true);
  };

  // Cosmetic reminders/orders handlers — unchanged from the new UI's
  // reference App.tsx, just lifted into local state because there's no
  // backend wiring for them yet.
  const handleToggleReminder = (id: string) =>
    setReminders((prev) => prev.map((r) => (r.id === id ? { ...r, completed: !r.completed } : r)));
  const handleAddReminder = (text: string, category: 'daily' | 'today') => {
    setReminders((prev) => [
      ...prev,
      {
        id: `rem-${Date.now()}`,
        category,
        text,
        time: category === 'daily' ? '08:30 AM' : 'Just now',
        completed: false,
      },
    ]);
  };
  const handleDeleteReminder = (id: string) =>
    setReminders((prev) => prev.filter((r) => r.id !== id));
  const handleClearCompletedReminders = () =>
    setReminders((prev) => prev.filter((r) => !r.completed));

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

  const remindersBadge = useMemo(
    () => reminders.filter((r) => !r.completed).length,
    [reminders],
  );

  const isAdmin = ADMIN_USERNAMES.has(username);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-slate-900 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Login onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <div className="flex h-[100dvh] w-screen overflow-hidden font-sans select-none relative">
      <LeftSidebar
        username={username}
        isAdmin={isAdmin}
        onLogout={handleLogout}
        onOpenAdmin={() => {
          setIsAdminOpen(true);
        }}
        onOpenSettings={() => openSettings('mcp')}
        onNewChat={handleNewChat}
        chats={chats}
        activeChatId={chatId}
        onSelectChat={handleSelectChat}
        onDeleteChat={handleDeleteChat}
        toolProviders={toolProviders}
        mcpStatuses={mcpStatuses}
        onSelectProvider={(id) => openSettings('mcp', id)}
        remindersBadge={remindersBadge || undefined}
        onShowReminders={() => setShowRemindersPopup(true)}
        onShowOrders={() => setShowOrdersPopup(true)}
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
            onVoiceTurn={handleVoiceTurn}
            onVoiceTurnComplete={handleVoiceTurnComplete}
            onVoiceToolCall={handleVoiceToolCall}
            onVoiceStateChange={handleVoiceStateChange}
            onVoiceError={handleVoiceError}
          />
        )}
      </div>

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
        {showRemindersPopup && (
          <RemindersPopup
            isOpen={showRemindersPopup}
            onClose={() => setShowRemindersPopup(false)}
            reminders={reminders}
            onToggleReminder={handleToggleReminder}
            onAddReminder={handleAddReminder}
            onDeleteReminder={handleDeleteReminder}
            onClearCompleted={handleClearCompletedReminders}
          />
        )}
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
  );
}

export default App;
