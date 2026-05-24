import { useState, useEffect } from 'react'
import { Login } from './Login'
import { ChatLog } from './ChatLog'
import { MessageInput } from './MessageInput'
import { SettingsModal } from './SettingsModal'
import { api, clearToken, getToken } from './api'
import type { GeoCoordinate, Message, QueryResponse, SessionAddress, SessionRestaurant, ToolProvider } from './types'
import { useUserLocation } from './useUserLocation'
import { Settings, LogOut, Sparkles } from 'lucide-react'
import './index.css'

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(!!getToken());
  const [username, setUsername] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);
  
  const [messages, setMessages] = useState<Message[]>([]);
  const [isThinking, setIsThinking] = useState(false);

  const [toolProviders, setToolProviders] = useState<ToolProvider[]>([]);

  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  // Carry-forward state from the backend across turns. Every successful
  // /api/agent/query/ returns the placeholder IDs (cart_id, restaurant_id,
  // order_id, ...) the executor learned this turn and the LRU index of
  // restaurants the user has been shown. We echo BOTH back on the next
  // request so the backend can resolve __CART_ID__ for checkout, skip a
  // re-search when the user picks a known restaurant by name, etc.
  // Without this echo, every multi-turn flow loses cart context — which
  // is the prod regression we just hit on houzzat.in.
  const [sessionState, setSessionState] = useState<Record<string, string>>({});
  const [sessionRestaurants, setSessionRestaurants] = useState<SessionRestaurant[]>([]);
  // The user's saved Zomato delivery addresses, fetched once by the backend
  // on the first food turn and then round-tripped through every subsequent
  // request so the executor never has to re-call get_saved_addresses_for_user.
  // This is what lets the chat "just work" without the user pressing the
  // address-fetch button: the very first zomato turn auto-resolves an
  // address, ships it back here, and turns 2+ reuse it. The user can still
  // change addresses by saying "use my work address" (planner picks from
  // this list) or by picking from the dropdown above the input.
  const [sessionAddresses, setSessionAddresses] = useState<SessionAddress[]>([]);

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
    try {
      const providersData = await api.get('/api/tools/providers/');
      setToolProviders(providersData.providers || []);
    } catch (err) {
      console.error('Failed to load initial data', err);
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
  };

  const buildAgentBody = (content: string, geo?: GeoCoordinate | null) => {
    const body: Record<string, any> = {
      message: content,
      // Address is auto-resolved server-side from session_addresses on the
      // very first turn and persisted in session_state across the rest of
      // the conversation. The frontend no longer pre-selects one.
      address_id: '',
      execute_tools: true,
      history: messages.map((m) => ({ role: m.role, content: m.content })),
    };
    if (geo) {
      body.geo = {
        latitude: geo.latitude,
        longitude: geo.longitude,
        ...(geo.accuracyMeters !== undefined ? { accuracy_meters: geo.accuracyMeters } : {}),
        captured_at: new Date().toISOString(),
      };
    }
    // Echo prior-turn state so the backend can resolve carried placeholders
    // (cart_id, restaurant_id, order_id) and reuse the restaurant index
    // instead of re-running an unreliable keyword search. Omit empty
    // values to keep the wire payload small for the very first turn.
    if (Object.keys(sessionState).length > 0) {
      body.session_state = sessionState;
    }
    if (sessionRestaurants.length > 0) {
      body.session_restaurants = sessionRestaurants;
    }
    if (sessionAddresses.length > 0) {
      body.session_addresses = sessionAddresses;
    }
    return body;
  };

  const handleSendMessage = async (content: string) => {
    const userMsg: Message = { role: 'user', content };
    setMessages(prev => [...prev, userMsg]);
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
          setMessages(prev => [...prev, { role: 'assistant', content: friendly[reason] || friendly.unknown }]);
          return;
        }
      }

      const assistantMsg: Message = {
        role: 'assistant',
        content: data.assistant_message,
        meta: [...data.selected_tool_providers, data.tool_execution.ok ? 'tools ok' : data.tool_execution.error].filter(Boolean) as string[],
        trace: data.observable_trace,
        attachments: Array.isArray(data.attachments) ? data.attachments : undefined,
      };

      // Persist the carry-forward state the backend just learned. Doing
      // this after the message is rendered guarantees we never echo back
      // a half-updated state if the user fires off another message
      // before the assistant message lands.
      if (data.session_state && typeof data.session_state === 'object') {
        setSessionState(data.session_state);
      }
      if (Array.isArray(data.session_restaurants)) {
        setSessionRestaurants(data.session_restaurants);
      }
      if (Array.isArray(data.session_addresses)) {
        setSessionAddresses(data.session_addresses);
      }

      setMessages(prev => [...prev, assistantMsg]);
    } catch (err: any) {
      const errorMsg: Message = {
        role: 'assistant',
        content: `Error: ${err.message || 'Something went wrong'}`,
      };
      setMessages(prev => [...prev, errorMsg]);
    } finally {
      setIsThinking(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-amber-400 border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Login onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <div className="min-h-screen bg-background text-gray-100 flex flex-col">
      <header className="h-16 border-b border-border bg-surface/60 backdrop-blur-md sticky top-0 z-20 flex items-center justify-between px-6">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 brand-gradient rounded-xl flex items-center justify-center text-gray-900 shadow-md shadow-amber-500/30">
            <Sparkles size={18} strokeWidth={2.5} />
          </div>
          <h1 className="text-lg font-bold tracking-tight text-white">Companion</h1>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex flex-col items-end mr-2">
            <span className="text-xs font-bold text-gray-100">{username}</span>
            <span className="text-[10px] text-amber-400 font-medium">Online</span>
          </div>
          <button
            onClick={() => setIsSettingsOpen(true)}
            title="Settings"
            className="p-2 rounded-xl hover:bg-surface text-gray-400 hover:text-amber-300 transition-all"
          >
            <Settings size={20} />
          </button>
          <button
            onClick={handleLogout}
            title="Logout"
            className="p-2 rounded-xl hover:bg-red-500/10 text-gray-400 hover:text-red-400 transition-all"
          >
            <LogOut size={20} />
          </button>
        </div>
      </header>

      <ChatLog
        messages={messages}
        isThinking={isThinking}
        sessionAddresses={sessionAddresses}
        onSelectAddress={(addr) => {
          // Single-click address switch — fires off a natural-language
          // "switch" turn the planner already knows how to handle (it
          // emits a literal address_id pulled from session_addresses,
          // and the executor records the switch in placeholder_values).
          const label = (addr.location_name || addr.address_id || '').trim();
          if (!label || isThinking) return;
          handleSendMessage(`Switch my delivery address to: ${label}.`);
        }}
      />

      <MessageInput
        onSendMessage={handleSendMessage}
        disabled={isThinking}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        toolProviders={toolProviders}
      />
    </div>
  )
}

export default App

