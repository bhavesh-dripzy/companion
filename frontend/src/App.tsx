import { useState, useEffect } from 'react'
import { Login } from './Login'
import { ChatLog } from './ChatLog'
import { MessageInput } from './MessageInput'
import { SettingsModal } from './SettingsModal'
import { api, clearToken, getToken } from './api'
import type { GeoCoordinate, Message, Address, QueryResponse, SessionRestaurant, ToolProvider } from './types'
import { useUserLocation } from './useUserLocation'
import { Settings, LogOut } from 'lucide-react'
import './index.css'

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(!!getToken());
  const [username, setUsername] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);
  
  const [messages, setMessages] = useState<Message[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [selectedAddressId, setSelectedAddressId] = useState('');
  const [isLoadingAddresses, setIsLoadingAddresses] = useState(false);
  
  const [toolProviders, setToolProviders] = useState<ToolProvider[]>([]);
  const [selectedToolProvider, setSelectedToolProvider] = useState('zomato');
  
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
      const providers = providersData.providers || [];
      setToolProviders(providers);
      if (providers.length > 0) {
        setSelectedToolProvider(providers[0].id);
      }
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
  };

  const fetchAddresses = async () => {
    setIsLoadingAddresses(true);
    try {
      const data = await api.get(`/api/food/saved-addresses/?provider=${selectedToolProvider}`);
      setAddresses(data.addresses || []);
      if (data.addresses?.length > 0) {
        setSelectedAddressId(data.addresses[0].address_id);
      }
    } catch (err) {
      console.error('Failed to fetch addresses', err);
    } finally {
      setIsLoadingAddresses(false);
    }
  };

  const buildAgentBody = (content: string, geo?: GeoCoordinate | null) => {
    const body: Record<string, any> = {
      message: content,
      address_id: selectedAddressId,
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
        <div className="w-8 h-8 border-4 border-accent border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Login onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <div className="min-h-screen bg-background text-gray-100 flex flex-col">
      <header className="h-16 border-b border-border bg-surface/50 backdrop-blur-md sticky top-0 z-20 flex items-center justify-between px-6">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-accent rounded-lg flex items-center justify-center text-white font-bold shadow-lg shadow-accent/20">
            C
          </div>
          <h1 className="text-lg font-bold tracking-tight">Companion</h1>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex flex-col items-end mr-2">
            <span className="text-xs font-bold text-gray-300">{username}</span>
            <span className="text-[10px] text-gray-500 font-medium">Online</span>
          </div>
          <button
            onClick={() => setIsSettingsOpen(true)}
            title="Settings"
            className="p-2 rounded-xl hover:bg-neutral-800 text-gray-400 hover:text-white transition-all"
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

      <ChatLog messages={messages} isThinking={isThinking} />

      <MessageInput
        onSendMessage={handleSendMessage}
        addresses={addresses}
        selectedAddressId={selectedAddressId}
        onAddressChange={setSelectedAddressId}
        onFetchAddresses={fetchAddresses}
        isLoadingAddresses={isLoadingAddresses}
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

