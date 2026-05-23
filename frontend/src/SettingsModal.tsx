import React, { useState, useEffect } from 'react';
import { X, Globe, Calendar, Zap, MapPin, Settings as SettingsIcon, CheckCircle2, AlertCircle, ExternalLink, RefreshCw, Loader2 } from 'lucide-react';
import { api } from './api';
import type { ToolProvider, McpStatus, MapsRouteSummary, MapsTravelMode } from './types';
import { cn } from './utils';
import { GeolocationError, computeRoute, mapsStatus } from './maps';

const MAPS_TRAVEL_MODES: { value: MapsTravelMode; label: string }[] = [
  { value: 'DRIVE', label: 'Drive' },
  { value: 'TWO_WHEELER', label: 'Two-wheeler' },
  { value: 'WALK', label: 'Walk' },
  { value: 'BICYCLE', label: 'Bicycle' },
  { value: 'TRANSIT', label: 'Transit' },
];

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  toolProviders: ToolProvider[];
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  toolProviders,
}) => {
  const [activeTab, setActiveTab] = useState<'mcp' | 'calendar' | 'models'>('mcp');
  const [selectedProviderId, setSelectedProviderId] = useState('zomato');
  const [mcpStatus, setMcpStatus] = useState<McpStatus | null>(null);
  const [isLoadingMcp, setIsLoadingMcp] = useState(false);
  const [pairingCommand, setPairingCommand] = useState('');
  
  const [gcalStatus, setGcalStatus] = useState<{connected: boolean, configured: boolean} | null>(null);

  const [mapsConfigured, setMapsConfigured] = useState<boolean | null>(null);
  const [mapsDestination, setMapsDestination] = useState('');
  const [mapsTravelMode, setMapsTravelMode] = useState<MapsTravelMode>('DRIVE');
  const [mapsLoading, setMapsLoading] = useState(false);
  const [mapsResult, setMapsResult] = useState<MapsRouteSummary | null>(null);
  const [mapsError, setMapsError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      checkMcpStatus();
      checkGcalStatus();
      checkMapsStatus();
    }
  }, [isOpen, selectedProviderId]);

  const checkMcpStatus = async () => {
    setIsLoadingMcp(true);
    try {
      const data = await api.get(`/api/mcp/status/?provider=${selectedProviderId}`);
      setMcpStatus(data);
    } catch (err) {
      console.error('Failed to check MCP status', err);
    } finally {
      setIsLoadingMcp(false);
    }
  };

  const checkGcalStatus = async () => {
    try {
      const data = await api.get('/api/google-calendar/status/');
      setGcalStatus(data);
    } catch (err) {
      console.error('Failed to check Google Calendar status', err);
    }
  };

  const checkMapsStatus = async () => {
    try {
      setMapsConfigured(await mapsStatus());
    } catch (err) {
      console.error('Failed to check Google Maps status', err);
      setMapsConfigured(false);
    }
  };

  const handleComputeRoute = async () => {
    const destination = mapsDestination.trim();
    if (!destination) {
      setMapsError('Type a destination first (e.g. "Phoenix Mall, Bangalore").');
      return;
    }
    setMapsLoading(true);
    setMapsError(null);
    setMapsResult(null);
    try {
      const route = await computeRoute({
        destinationText: destination,
        travelMode: mapsTravelMode,
      });
      setMapsResult(route);
    } catch (err: any) {
      if (err instanceof GeolocationError) {
        const friendly: Record<string, string> = {
          permission_denied: 'Location permission was denied. Enable it in your browser to compute distances.',
          position_unavailable: 'Your location is currently unavailable. Try again or move to a better signal.',
          timeout: 'Getting your location timed out. Please try again.',
          unsupported: 'This browser does not support Geolocation.',
          unknown: 'Could not read your location.',
        };
        setMapsError(friendly[err.reason] || err.message);
      } else {
        setMapsError(err?.message || 'Could not compute route.');
      }
    } finally {
      setMapsLoading(false);
    }
  };

  const handleConnectMcp = async () => {
    try {
      const data = await api.get(`/api/mcp/oauth/start?provider=${selectedProviderId}`);
      if (data.command) {
        setPairingCommand(data.command);
        return;
      }
      if (data.authorization_url) {
        window.location.assign(data.authorization_url);
        return;
      }
      
      const pairing = await api.post(`/api/mcp/pairing/?provider=${selectedProviderId}`);
      if (pairing.command) {
        setPairingCommand(pairing.command);
      }
    } catch (err) {
      console.error('Failed to connect MCP', err);
    }
  };

  const handleConnectGcal = async () => {
    try {
      const data = await api.get('/api/google-calendar/oauth/start');
      if (data.authorization_url) {
        window.open(data.authorization_url, '_blank');
      }
    } catch (err) {
      console.error('Failed to connect Google Calendar', err);
    }
  };

  if (!isOpen) return null;

  const tabs = [
    { id: 'mcp', label: 'MCP Connectors', icon: Globe },
    { id: 'calendar', label: 'Integrations', icon: Calendar },
    { id: 'models', label: 'Preferences', icon: SettingsIcon },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-gray-900/40 backdrop-blur-sm">
      <div className="w-full max-w-2xl bg-surface border border-border rounded-3xl shadow-[0_32px_80px_-24px_rgba(99,102,241,0.35)] flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-8 py-6 border-b border-border">
          <h2 className="text-xl font-bold flex items-center gap-2 text-gray-900">
            <SettingsIcon size={20} className="text-violet-500" />
            Settings
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-full transition-colors text-gray-500">
            <X size={20} />
          </button>
        </div>

        <div className="flex flex-1 overflow-hidden">
          {/* Sidebar */}
          <div className="w-48 border-r border-border p-4 space-y-2">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={cn(
                  "w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-semibold transition-all",
                  activeTab === tab.id
                    ? "bg-violet-50 text-violet-700"
                    : "text-gray-600 hover:text-gray-900 hover:bg-gray-50"
                )}
              >
                <tab.icon size={18} />
                {tab.label}
              </button>
            ))}
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-8">
            {activeTab === 'mcp' && (
              <div className="space-y-8">
                <div>
                  <label className="block text-sm font-bold text-gray-700 mb-4 uppercase tracking-wider">Select Provider</label>
                  <div className="grid grid-cols-2 gap-3">
                    {toolProviders.map((p) => (
                      <button
                        key={p.id}
                        onClick={() => setSelectedProviderId(p.id)}
                        className={cn(
                          "p-4 rounded-2xl border text-left transition-all",
                          selectedProviderId === p.id
                            ? "border-violet-300 bg-violet-50 ring-2 ring-violet-100"
                            : "border-border hover:border-violet-200 bg-gray-50"
                        )}
                      >
                        <div className="font-bold text-sm mb-1 text-gray-900">{p.label}</div>
                        <div className="text-[10px] text-gray-500 uppercase font-black">{p.available ? 'Ready' : 'Setup Required'}</div>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="p-6 bg-gray-50 border border-border rounded-2xl space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-gray-900">Status:</span>
                      {mcpStatus?.status === 'ready' ? (
                        <div className="flex items-center gap-1 text-emerald-600 text-xs font-bold uppercase">
                          <CheckCircle2 size={14} /> Connected
                        </div>
                      ) : (
                        <div className="flex items-center gap-1 text-amber-600 text-xs font-bold uppercase">
                          <AlertCircle size={14} /> {mcpStatus?.status || 'Unknown'}
                        </div>
                      )}
                    </div>
                    <button
                      onClick={checkMcpStatus}
                      className="p-2 hover:bg-white rounded-lg text-gray-500 transition-colors"
                    >
                      <RefreshCw size={14} className={isLoadingMcp ? 'animate-spin' : ''} />
                    </button>
                  </div>

                  <p className="text-xs text-gray-600 leading-relaxed">
                    {mcpStatus?.detail || 'No status details available.'}
                  </p>

                  <div className="pt-2 flex gap-3">
                    <button
                      onClick={handleConnectMcp}
                      className="flex-1 brand-gradient text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-md shadow-violet-500/25 hover:opacity-95"
                    >
                      Connect Account
                    </button>
                    <button className="px-6 border border-border hover:bg-gray-100 text-gray-700 text-sm font-bold rounded-xl transition-all">
                      Test
                    </button>
                  </div>
                </div>

                {pairingCommand && (
                  <div className="space-y-3">
                    <label className="block text-[10px] font-black uppercase text-gray-500 tracking-tighter">Connection Command</label>
                    <div className="p-4 bg-gray-900 rounded-xl border border-gray-800 font-mono text-[11px] text-violet-300 break-all overflow-hidden relative group">
                      {pairingCommand}
                      <button
                        onClick={() => navigator.clipboard.writeText(pairingCommand)}
                        className="absolute right-2 top-2 p-1.5 bg-gray-800 rounded-md opacity-0 group-hover:opacity-100 transition-opacity text-violet-300"
                      >
                        <Zap size={12} />
                      </button>
                    </div>
                    <p className="text-[10px] text-gray-500 italic">Run this in your terminal to complete pairing.</p>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'calendar' && (
              <div className="space-y-8">
                <div className="p-6 bg-gray-50 border border-border rounded-3xl flex items-start gap-5">
                  <div className="w-12 h-12 bg-blue-100 rounded-2xl flex items-center justify-center text-blue-600 flex-shrink-0">
                    <Calendar size={24} />
                  </div>
                  <div className="flex-1 space-y-4">
                    <div>
                      <h3 className="font-bold text-gray-900">Google Calendar</h3>
                      <p className="text-xs text-gray-600 mt-1 leading-relaxed">Sync your reminders directly to your Google Calendar.</p>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-black uppercase text-gray-500">Status:</span>
                      {gcalStatus?.connected ? (
                        <span className="text-[10px] font-black uppercase text-emerald-600 flex items-center gap-1">
                          <CheckCircle2 size={12} /> Connected
                        </span>
                      ) : (
                        <span className="text-[10px] font-black uppercase text-gray-500">Not Connected</span>
                      )}
                    </div>

                    <button
                      onClick={handleConnectGcal}
                      className="w-full bg-gray-900 text-white hover:bg-gray-800 text-sm font-bold py-2.5 rounded-xl transition-all flex items-center justify-center gap-2"
                    >
                      {gcalStatus?.connected ? 'Reconnect Google' : 'Connect Google Calendar'}
                      <ExternalLink size={14} />
                    </button>
                  </div>
                </div>

                <div className="p-6 bg-gray-50 border border-border rounded-3xl flex items-start gap-5">
                  <div className="w-12 h-12 bg-emerald-100 rounded-2xl flex items-center justify-center text-emerald-600 flex-shrink-0">
                    <MapPin size={24} />
                  </div>
                  <div className="flex-1 space-y-4 min-w-0">
                    <div>
                      <h3 className="font-bold text-gray-900">Google Maps</h3>
                      <p className="text-xs text-gray-600 mt-1 leading-relaxed">
                        Distance and ETA from your current location. Your browser will ask for location permission the first time you use it.
                      </p>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-black uppercase text-gray-500">Status:</span>
                      {mapsConfigured === null ? (
                        <span className="text-[10px] font-black uppercase text-gray-500">Checking…</span>
                      ) : mapsConfigured ? (
                        <span className="text-[10px] font-black uppercase text-emerald-600 flex items-center gap-1">
                          <CheckCircle2 size={12} /> Ready
                        </span>
                      ) : (
                        <span className="text-[10px] font-black uppercase text-amber-600 flex items-center gap-1">
                          <AlertCircle size={12} /> Not configured
                        </span>
                      )}
                    </div>

                    <div className="space-y-3">
                      <input
                        type="text"
                        value={mapsDestination}
                        onChange={(e) => setMapsDestination(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') handleComputeRoute(); }}
                        placeholder="Where to? e.g. Phoenix Mall, Bangalore"
                        disabled={!mapsConfigured || mapsLoading}
                        className="w-full bg-white border border-border rounded-xl px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:border-violet-300 focus:ring-4 focus:ring-violet-100 focus:outline-none disabled:opacity-50"
                      />

                      <div className="flex flex-wrap gap-2">
                        {MAPS_TRAVEL_MODES.map((mode) => (
                          <button
                            key={mode.value}
                            type="button"
                            onClick={() => setMapsTravelMode(mode.value)}
                            disabled={mapsLoading}
                            className={cn(
                              'px-3 py-1 rounded-lg text-[11px] font-bold uppercase tracking-wider transition-all',
                              mapsTravelMode === mode.value
                                ? 'bg-emerald-100 text-emerald-700 border border-emerald-300'
                                : 'border border-border text-gray-500 hover:text-gray-800 bg-white',
                            )}
                          >
                            {mode.label}
                          </button>
                        ))}
                      </div>

                      <button
                        onClick={handleComputeRoute}
                        disabled={!mapsConfigured || mapsLoading || !mapsDestination.trim()}
                        className="w-full bg-emerald-500 hover:bg-emerald-600 text-white text-sm font-bold py-2.5 rounded-xl transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {mapsLoading ? (
                          <><Loader2 size={14} className="animate-spin" /> Computing…</>
                        ) : (
                          <>Show distance &amp; ETA <MapPin size={14} /></>
                        )}
                      </button>

                      {mapsError && (
                        <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 leading-relaxed">
                          {mapsError}
                        </div>
                      )}

                      {mapsResult && (
                        <div className="p-4 bg-white border border-emerald-200 rounded-2xl space-y-2">
                          <div className="flex items-baseline justify-between">
                            <span className="text-[10px] font-black uppercase text-gray-500">Distance</span>
                            <span className="font-bold text-gray-900">{mapsResult.distance_text}</span>
                          </div>
                          <div className="flex items-baseline justify-between">
                            <span className="text-[10px] font-black uppercase text-gray-500">ETA</span>
                            <span className="font-bold text-emerald-600">{mapsResult.duration_text}</span>
                          </div>
                          {mapsResult.description && (
                            <p className="text-[11px] text-gray-500 italic pt-1 border-t border-border">
                              {mapsResult.description}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <div className="p-6 bg-gray-50 border border-border rounded-3xl flex items-start gap-5 opacity-60 grayscale">
                  <div className="w-12 h-12 bg-orange-100 rounded-2xl flex items-center justify-center text-orange-600 flex-shrink-0">
                    <Zap size={24} />
                  </div>
                  <div className="flex-1 space-y-4">
                    <div>
                      <h3 className="font-bold text-gray-900">Zapier MCP</h3>
                      <p className="text-xs text-gray-600 mt-1 leading-relaxed">Connect to 6,000+ apps via Zapier's MCP server.</p>
                    </div>
                    <button disabled className="w-full border border-border text-gray-500 text-sm font-bold py-2.5 rounded-xl">
                      Coming Soon
                    </button>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'models' && (
              <div className="space-y-6">
                <div>
                  <label className="block text-sm font-bold text-gray-700 mb-4 uppercase tracking-wider">AI Model Provider</label>
                  <div className="space-y-3">
                    {['Auto-detect', 'Gemini', 'OpenAI', 'DeepSeek'].map((provider) => (
                      <button
                        key={provider}
                        className={cn(
                          "w-full p-4 rounded-2xl border text-left transition-all flex items-center justify-between",
                          provider === 'Auto-detect'
                            ? "border-violet-300 bg-violet-50 ring-2 ring-violet-100"
                            : "border-border hover:border-violet-200 bg-white"
                        )}
                      >
                        <span className="font-bold text-sm text-gray-900">{provider}</span>
                        {provider === 'Auto-detect' && <CheckCircle2 size={16} className="text-violet-600" />}
                      </button>
                    ))}
                  </div>
                </div>

                <p className="text-xs text-gray-500 italic px-2 leading-relaxed">
                  Companion automatically routes requests to the best available model. You can override this setting to force a specific provider.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
