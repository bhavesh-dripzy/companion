import { useEffect, useState } from 'react';
import { Login } from './Login';
import { ChatLog } from './ChatLog';
import { MessageInput } from './MessageInput';
import { SettingsModal } from './SettingsModal';
import { SidebarRail } from './SidebarRail';
import { RightSidebar } from './RightSidebar';
import { AdminDashboard } from './AdminDashboard';
import { api, clearToken, getToken } from './api';
import type {
  ChatSummary,
  GeoCoordinate,
  McpStatus,
  Message,
  QueryResponse,
  SessionAddress,
  SessionCart,
  SessionMenuEntry,
  SessionRestaurant,
  ToolProvider,
} from './types';
import { useUserLocation } from './useUserLocation';
import { mapsStatus } from './maps';
import { Menu, PanelRightOpen, Search, Sparkles, X } from 'lucide-react';
import './index.css';

// localStorage key holding the chat_id the user was last viewing. We
// restore it on reload so the workspace lands back in the same
// conversation; it gets cleared by "new chat" / logout, and overwritten
// every time the backend confirms (or lazy-creates) a chat_id.
const ACTIVE_CHAT_STORAGE_KEY = 'companion.active_chat_id';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(!!getToken());
  const [username, setUsername] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);

  const [messages, setMessages] = useState<Message[]>([]);
  const [isThinking, setIsThinking] = useState(false);

  const [toolProviders, setToolProviders] = useState<ToolProvider[]>([]);

  // Lifted from SettingsModal so the RightSidebar can render live MCP /
  // integration status without each panel running its own poller. Both
  // panels read from this same dictionary; SettingsModal still owns the
  // mutating actions (connect / disconnect / pair).
  const [mcpStatuses, setMcpStatuses] = useState<Record<string, McpStatus>>({});
  const [isLoadingMcp, setIsLoadingMcp] = useState(false);
  const [gcalStatus, setGcalStatus] = useState<{ connected: boolean; configured: boolean } | null>(null);
  const [mapsConfigured, setMapsConfigured] = useState<boolean | null>(null);

  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isAdminOpen, setIsAdminOpen] = useState(false);
  const [settingsInitialTab, setSettingsInitialTab] = useState<'mcp' | 'calendar' | 'models'>('mcp');
  const [settingsInitialProvider, setSettingsInitialProvider] = useState<string | undefined>(undefined);

  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [showQuickActions, setShowQuickActions] = useState(true);

  // Mobile drawer state. Both sidebars are slide-in panels under
  // `md` (768px) — the rail opens with the hamburger and the right
  // panel opens with the panel-icon on the mobile top bar. We never
  // open both at once; tapping one closes the other to keep the
  // chat readable behind the backdrop.
  const [mobileRailOpen, setMobileRailOpen] = useState(false);
  const [mobileRightOpen, setMobileRightOpen] = useState(false);

  // Carry-forward state from the backend across turns. See the inline
  // notes in the previous skin: each successful /api/agent/query/ ships
  // the placeholder IDs (cart_id, restaurant_id, order_id) the executor
  // learned this turn and the LRU index of restaurants the user has
  // been shown. We echo both back on the next request so the backend
  // can resolve __CART_ID__ for checkout, skip a re-search when the
  // user picks a known restaurant by name, etc.
  const [sessionState, setSessionState] = useState<Record<string, string>>({});
  const [sessionRestaurants, setSessionRestaurants] = useState<SessionRestaurant[]>([]);
  // Saved Zomato delivery addresses, fetched once on the first food
  // turn and then round-tripped through every subsequent request.
  const [sessionAddresses, setSessionAddresses] = useState<SessionAddress[]>([]);
  // Cross-turn LRU of menu items the user has been shown this session.
  // The backend echoes 8 items per restaurant the planner has seen;
  // we MUST round-trip this so a follow-up like "yes plz add" can be
  // resolved against the literal variant_id from the prior turn rather
  // than the planner hallucinating a stale id. (Skipping this echo is
  // exactly the bug that made "K - Whopper Twin Meal Deal" fail with
  // "Variant ID … not found in restaurant menu" across several turns.)
  const [sessionMenu, setSessionMenu] = useState<SessionMenuEntry[]>([]);
  // Current Zomato cart snapshot — replaced on each successful create_cart,
  // carried unchanged on every other turn. Round-tripping gives the response
  // LLM ground truth for "what's in my cart" without inferring from chat
  // history (Zomato carts are single-restaurant; the prior cart is gone the
  // moment the user adds an item from a different restaurant).
  const [sessionCart, setSessionCart] = useState<SessionCart | null>(null);

  // Multi-chat support. `chats` is the full sidebar list (most-recent
  // first), `chatId` is the conversation the workspace is currently
  // showing. `chatId === null` means "fresh new chat about to be
  // started" — no Dynamo row exists yet; the first /api/agent/query/
  // call will lazy-create one and return its id, which we'll then pin.
  // We persist `chatId` in localStorage so a page reload lands the
  // user back in the same conversation instead of dumping them into a
  // blank new chat (or worse, the previous chat's stale history).
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

  const loadInitialData = async () => {
    // Boot order matters slightly: the chat list has to land before we
    // try to restore the persisted chat_id, so the sidebar can highlight
    // the active row from the very first render. Messages load second
    // and can lag behind without UX cost.
    await refreshChatList();
    if (chatId) {
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
      // If the localStorage-pinned chat no longer exists on the server
      // (deleted on another device, scrubbed by the backend, etc.) drop
      // the stale id so the workspace doesn't load a ghost.
      if (chatId && !list.some((c) => c.chat_id === chatId)) {
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

  // Switch the workspace to a different saved chat: pin the id, fetch
  // its messages, and drop all in-memory carry-forward session state
  // (cart, restaurants, addresses, menu LRU, placeholders). Those
  // belong to the chat we're leaving — Zomato cart for chat A has no
  // business polluting chat B's add-to-cart flow.
  const handleSelectChat = async (newChatId: string) => {
    if (newChatId === chatId) return;
    setChatId(newChatId);
    localStorage.setItem(ACTIVE_CHAT_STORAGE_KEY, newChatId);
    setIsAdminOpen(false);
    setSessionState({});
    setSessionRestaurants([]);
    setSessionAddresses([]);
    setSessionMenu([]);
    setSessionCart(null);
    setSearchQuery('');
    setSearchOpen(false);
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
    // If the deleted chat was the active one, fall back to "new chat"
    // mode — clear the workspace and the localStorage pin.
    if (targetChatId === chatId) {
      setChatId(null);
      localStorage.removeItem(ACTIVE_CHAT_STORAGE_KEY);
      setMessages([]);
      setSessionState({});
      setSessionRestaurants([]);
      setSessionAddresses([]);
      setSessionMenu([]);
      setSessionCart(null);
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
    setChats([]);
    setChatId(null);
    localStorage.removeItem(ACTIVE_CHAT_STORAGE_KEY);
  };

  const handleNewChat = () => {
    // Lazy chat creation: we don't POST /api/chats/ here. The chat row
    // only gets written to Dynamo once the user actually sends a turn,
    // so users can spam "new chat" without polluting the sidebar with
    // empty rows. The backend lazy-creates on the first /api/agent/query/
    // request that lacks a chat_id and echoes the new id back.
    setIsAdminOpen(false);
    setMessages([]);
    setSessionState({});
    setSessionRestaurants([]);
    setSessionAddresses([]);
    setSessionMenu([]);
    setSessionCart(null);
    setSearchQuery('');
    setSearchOpen(false);
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
    if (chatId) body.chat_id = chatId;
    return body;
  };

  const handleSendMessage = async (content: string) => {
    const userMsg: Message = { role: 'user', content };
    setMessages((prev) => [...prev, userMsg]);
    setIsThinking(true);

    try {
      let data: QueryResponse = await api.post(
        '/api/agent/query/',
        buildAgentBody(content, userLocation),
      );

      const errorCode = (data as any)?.tool_execution?.error_code;
      if (errorCode === 'needs_location') {
        try {
          const fresh = await ensureLocation();
          data = await api.post('/api/agent/query/', buildAgentBody(content, fresh));
        } catch (geoErr: any) {
          const reason = geoErr?.reason ?? 'unknown';
          const friendly: Record<string, string> = {
            permission_denied: "I couldn't get your location — you blocked the request. Allow location access in your browser and try again.",
            position_unavailable: 'Your location is currently unavailable. Try again with a better signal.',
            timeout: 'Getting your location timed out — please try again.',
            unsupported: 'This browser does not expose location services.',
            unknown: 'I could not read your location.',
          };
          setMessages((prev) => [...prev, { role: 'assistant', content: friendly[reason] || friendly.unknown }]);
          return;
        }
      }

      const assistantMsg: Message = {
        role: 'assistant',
        content: data.assistant_message,
        meta: [...data.selected_tool_providers, data.tool_execution.ok ? 'tools ok' : data.tool_execution.error].filter(Boolean) as string[],
        trace: data.observable_trace,
        attachments: Array.isArray(data.attachments) ? data.attachments : undefined,
        provider:
          typeof data.routed_provider === 'string' && data.routed_provider ? data.routed_provider : undefined,
      };

      if (data.session_state && typeof data.session_state === 'object') setSessionState(data.session_state);
      if (Array.isArray(data.session_restaurants)) setSessionRestaurants(data.session_restaurants);
      if (Array.isArray(data.session_addresses)) setSessionAddresses(data.session_addresses);
      if (Array.isArray(data.session_menu)) setSessionMenu(data.session_menu);
      // session_cart is replaced wholesale (null on a fresh session, full
      // dict after each successful create_cart). null means "no current
      // cart" — clear it so the prior cart doesn't leak across restaurants.
      if ('session_cart' in data) {
        const c = (data as any).session_cart;
        setSessionCart(c && typeof c === 'object' && c.cart_id ? c : null);
      }

      // Pin the chat_id the backend confirmed (or lazy-created) so all
      // subsequent turns land in the same conversation. Refresh the
      // sidebar list so this chat sorts to the top with a fresh preview
      // — and so a newly-created chat actually shows up.
      if (typeof data.chat_id === 'string' && data.chat_id) {
        if (data.chat_id !== chatId) {
          setChatId(data.chat_id);
          localStorage.setItem(ACTIVE_CHAT_STORAGE_KEY, data.chat_id);
        }
        // Don't await — let the UI render the assistant message first;
        // a stale sidebar list for a couple hundred ms is fine.
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

  const openSettings = (tab: 'mcp' | 'calendar' | 'models' = 'mcp', providerId?: string) => {
    setSettingsInitialTab(tab);
    setSettingsInitialProvider(providerId);
    setIsSettingsOpen(true);
  };

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

  const openMobileRail = () => {
    setMobileRightOpen(false);
    setMobileRailOpen(true);
  };
  const openMobileRight = () => {
    setMobileRailOpen(false);
    setMobileRightOpen(true);
  };

  return (
    <div className="h-[100dvh] w-screen flex overflow-hidden text-slate-900">
      <SidebarRail
        onNewChat={handleNewChat}
        onToggleSearch={() => setSearchOpen((v) => !v)}
        onToggleExplore={() => setShowQuickActions((v) => !v)}
        onOpenSettings={() => openSettings('mcp')}
        onLogout={handleLogout}
        searchActive={searchOpen}
        exploreActive={showQuickActions}
        isMobileOpen={mobileRailOpen}
        onMobileClose={() => setMobileRailOpen(false)}
        isAdmin={['admin', 'admin_bhavesh', 'admin_kshitij', 'local_admin'].includes(username)}
        onOpenAdmin={() => {
          setSearchOpen(false);
          setIsAdminOpen(true);
        }}
        chats={chats}
        activeChatId={chatId}
        onSelectChat={handleSelectChat}
        onDeleteChat={handleDeleteChat}
      />

      <div className="flex-1 flex flex-col relative overflow-hidden min-w-0">
        {/* Mobile top bar — only visible below `md`. Hamburger opens the
            rail, the right-panel icon opens the integrations panel, and
            the brand text gives the user a focal anchor. */}
        <div className="md:hidden flex items-center justify-between px-3 py-2.5 bg-white/70 backdrop-blur-md border-b border-slate-200/70 z-20 shrink-0">
          <button
            onClick={openMobileRail}
            className="w-10 h-10 rounded-full text-slate-700 hover:text-slate-900 bg-slate-100/60 hover:bg-slate-100 flex items-center justify-center transition-all active:scale-95"
            title="Open menu"
            aria-label="Open menu"
          >
            <Menu size={20} />
          </button>

          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-full bg-slate-950 text-white flex items-center justify-center shadow">
              <Sparkles size={12} className="text-amber-300" />
            </div>
            <span className="text-sm font-bold text-slate-900 tracking-tight">Companion</span>
          </div>

          <button
            onClick={openMobileRight}
            className="w-10 h-10 rounded-full text-slate-700 hover:text-slate-900 bg-slate-100/60 hover:bg-slate-100 flex items-center justify-center transition-all active:scale-95"
            title="Open integrations panel"
            aria-label="Open integrations panel"
          >
            <PanelRightOpen size={18} />
          </button>
        </div>

        {isAdminOpen ? (
          <AdminDashboard onClose={() => setIsAdminOpen(false)} />
        ) : (
          <>
            {searchOpen && (
              <div className="absolute top-16 md:top-4 left-1/2 -translate-x-1/2 z-30 flex items-center gap-2 bg-white border border-slate-200 rounded-full shadow-lg shadow-slate-900/10 pl-3 pr-1 py-1 max-w-[92vw]">
                <Search size={14} className="text-slate-500 shrink-0" />
                <input
                  autoFocus
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search this conversation…"
                  className="bg-transparent text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none w-48 md:w-64"
                />
                <button
                  onClick={() => {
                    setSearchOpen(false);
                    setSearchQuery('');
                  }}
                  className="p-1.5 text-slate-500 hover:text-slate-900 hover:bg-slate-100 rounded-full transition-colors shrink-0"
                  title="Close search"
                >
                  <X size={14} />
                </button>
              </div>
            )}

            <ChatLog
              messages={messages}
              isThinking={isThinking}
              sessionAddresses={sessionAddresses}
              onSelectAddress={(addr) => {
                const label = (addr.location_name || addr.address_id || '').trim();
                if (!label || isThinking) return;
                handleSendMessage(`Switch my delivery address to: ${label}.`);
              }}
              searchQuery={searchQuery}
              onQuickPrompt={(prompt) => {
                if (isThinking) return;
                handleSendMessage(prompt);
              }}
              quickPromptDisabled={isThinking}
            />

            <MessageInput
              onSendMessage={handleSendMessage}
              disabled={isThinking}
              showQuickActions={showQuickActions && messages.length === 0}
            />
          </>
        )}
      </div>

      <RightSidebar
        username={username}
        toolProviders={toolProviders}
        mcpStatuses={mcpStatuses}
        isLoadingMcp={isLoadingMcp}
        onSelectProvider={(id) => openSettings('mcp', id)}
        gcalStatus={gcalStatus}
        onConnectGcal={() => openSettings('calendar')}
        mapsConfigured={mapsConfigured}
        onOpenMaps={() => openSettings('calendar')}
        onOpenSettings={() => openSettings('mcp')}
        isMobileOpen={mobileRightOpen}
        onMobileClose={() => setMobileRightOpen(false)}
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

    </div>
  );
}

export default App;
