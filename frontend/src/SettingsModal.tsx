import React, { useState, useEffect } from 'react';
import { X, Globe, Calendar, Zap, MapPin, Settings as SettingsIcon, CheckCircle2, AlertCircle, ExternalLink, RefreshCw, Loader2, Unplug } from 'lucide-react';
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

// A provider is "Connected" if it has any usable credential — either an
// active token file on the Lambda's /tmp disk (`has_local_tokens`) OR a
// durably-persisted copy in AWS Secrets Manager (`has_secret_tokens`).
// The backend auto-restores from Secrets on cold starts, so both states
// are functionally identical from the user's point of view.
function isProviderConnected(status?: McpStatus | null): boolean {
  if (!status) return false;
  const s = status.status;
  if (s === 'ready') return true;
  // ready_pending = tokens live in Secrets Manager; backend will restore
  // them transparently before the next MCP call. Surfacing this as
  // "disconnected" is what caused users to think connecting Swiggy was
  // wiping their Zomato session after a Lambda cold start.
  if (s === 'ready_pending') return true;
  // Belt-and-braces: if either token store has the file, treat as connected.
  if ((status as any).has_local_tokens || (status as any).has_secret_tokens) return true;
  return false;
}

// Pairing commands are returned by the backend with a short TTL (the
// underlying mcp-remote session expires in a few minutes), but the user
// often hard-refreshes the browser between clicking Connect and pasting
// into a terminal. We persist them per-provider so a refresh doesn't
// lose the freshly-issued command — without this the user has to click
// Connect again, generating a new code and forgetting the old terminal.
const PAIRING_STORAGE_KEY = 'companion_mcp_pairing_commands';

function readStoredPairings(): Record<string, string> {
  try {
    const raw = localStorage.getItem(PAIRING_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeStoredPairings(map: Record<string, string>): void {
  try {
    localStorage.setItem(PAIRING_STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* ignore quota / privacy-mode failures */
  }
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  toolProviders,
}) => {
  const [activeTab, setActiveTab] = useState<'mcp' | 'calendar' | 'models'>('mcp');
  const [selectedProviderId, setSelectedProviderId] = useState('zomato');
  const [mcpStatuses, setMcpStatuses] = useState<Record<string, McpStatus>>({});
  const [isLoadingMcp, setIsLoadingMcp] = useState(false);
  // Pairing command is keyed per-provider so the user can have multiple
  // outstanding terminals open (one for Zomato, one for Swiggy, etc.)
  // without clobbering each other.
  const [pairingCommands, setPairingCommands] = useState<Record<string, string>>(() => readStoredPairings());

  const [gcalStatus, setGcalStatus] = useState<{connected: boolean, configured: boolean} | null>(null);

  const [mapsConfigured, setMapsConfigured] = useState<boolean | null>(null);
  const [mapsDestination, setMapsDestination] = useState('');
  const [mapsTravelMode, setMapsTravelMode] = useState<MapsTravelMode>('DRIVE');
  const [mapsLoading, setMapsLoading] = useState(false);
  const [mapsResult, setMapsResult] = useState<MapsRouteSummary | null>(null);
  const [mapsError, setMapsError] = useState<string | null>(null);

  const mcpStatus: McpStatus | null = mcpStatuses[selectedProviderId] || null;
  const pairingCommand: string = pairingCommands[selectedProviderId] || '';

  useEffect(() => {
    if (isOpen) {
      checkAllMcpStatuses();
      checkGcalStatus();
      checkMapsStatus();
    }
    // We intentionally do NOT re-run on selectedProviderId changes: the
    // bulk endpoint already returned everyone's status, and re-fetching
    // per-provider on every tile click is what caused the previous
    // version to flicker pairings off-and-on during the round-trip.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const checkAllMcpStatuses = async () => {
    setIsLoadingMcp(true);
    try {
      const data = await api.get('/api/mcp/status/all/');
      const list: McpStatus[] = Array.isArray(data?.statuses) ? data.statuses : [];
      const next: Record<string, McpStatus> = {};
      for (const entry of list) {
        const id = (entry as any).provider;
        if (typeof id === 'string' && id) {
          next[id] = entry;
        }
      }
      setMcpStatuses(next);
    } catch (err) {
      console.error('Failed to load MCP statuses', err);
    } finally {
      setIsLoadingMcp(false);
    }
  };

  const refreshSelectedStatus = async () => {
    setIsLoadingMcp(true);
    try {
      const data = await api.get(`/api/mcp/status/?provider=${selectedProviderId}`);
      setMcpStatuses((prev) => ({ ...prev, [selectedProviderId]: data }));
      // Once we observe a connected state, clear any stale pairing command
      // for that provider so the UI stops nagging the user to run it.
      if (isProviderConnected(data)) {
        setPairingCommands((prev) => {
          if (!prev[selectedProviderId]) return prev;
          const next = { ...prev };
          delete next[selectedProviderId];
          writeStoredPairings(next);
          return next;
        });
      }
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

  const recordPairingCommand = (providerId: string, command: string) => {
    setPairingCommands((prev) => {
      const next = { ...prev, [providerId]: command };
      writeStoredPairings(next);
      return next;
    });
  };

  const handleConnectMcp = async () => {
    try {
      const data = await api.get(`/api/mcp/oauth/start?provider=${selectedProviderId}`);
      if (data.command) {
        recordPairingCommand(selectedProviderId, data.command);
        return;
      }
      if (data.authorization_url) {
        window.location.assign(data.authorization_url);
        return;
      }

      const pairing = await api.post(`/api/mcp/pairing/?provider=${selectedProviderId}`);
      if (pairing.command) {
        recordPairingCommand(selectedProviderId, pairing.command);
      }
    } catch (err) {
      console.error('Failed to connect MCP', err);
    }
  };

  const handleDisconnectMcp = async () => {
    const provider = toolProviders.find((p) => p.id === selectedProviderId);
    const label = provider?.label || selectedProviderId;
    if (!window.confirm(`Disconnect ${label}? Other connected MCPs will stay linked.`)) {
      return;
    }
    setIsLoadingMcp(true);
    try {
      await api.post(`/api/mcp/disconnect/?provider=${selectedProviderId}`);
      // Clear any cached pairing command for this provider and re-fetch
      // the bulk status so every tile updates at once (otherwise the
      // green pill would linger on the just-disconnected provider until
      // the user toggled the modal closed and back open).
      setPairingCommands((prev) => {
        if (!prev[selectedProviderId]) return prev;
        const next = { ...prev };
        delete next[selectedProviderId];
        writeStoredPairings(next);
        return next;
      });
      await checkAllMcpStatuses();
    } catch (err) {
      console.error('Failed to disconnect MCP', err);
    } finally {
      setIsLoadingMcp(false);
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-2xl bg-surface border border-border rounded-3xl shadow-2xl shadow-black/60 flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-8 py-6 border-b border-border">
          <h2 className="text-xl font-bold flex items-center gap-2 text-white">
            <SettingsIcon size={20} className="text-amber-400" />
            Settings
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-surface-elevated rounded-full transition-colors text-gray-400 hover:text-white">
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
                    ? "bg-amber-500/10 text-amber-300"
                    : "text-gray-400 hover:text-white hover:bg-surface-elevated"
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
                  <label className="block text-sm font-bold text-gray-300 mb-4 uppercase tracking-wider">Select Provider</label>
                  <div className="grid grid-cols-2 gap-3">
                    {toolProviders.map((p) => {
                      const status = mcpStatuses[p.id];
                      const connected = isProviderConnected(status);
                      return (
                        <button
                          key={p.id}
                          onClick={() => setSelectedProviderId(p.id)}
                          className={cn(
                            "p-4 rounded-2xl border text-left transition-all relative",
                            selectedProviderId === p.id
                              ? "border-amber-500/60 bg-amber-500/10"
                              : "border-border hover:border-border-strong bg-surface-elevated"
                          )}
                        >
                          <div className="flex items-center justify-between gap-2 mb-1">
                            <div className="font-bold text-sm text-white">{p.label}</div>
                            {connected && (
                              <span className="flex items-center gap-1 text-emerald-400 text-[9px] font-black uppercase tracking-wider">
                                <CheckCircle2 size={10} /> Connected
                              </span>
                            )}
                          </div>
                          <div className="text-[10px] text-gray-500 uppercase font-black">
                            {!p.available ? 'Setup Required' : connected ? 'Linked' : 'Ready'}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="p-6 bg-surface-elevated border border-border rounded-2xl space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-white">Status:</span>
                      {isProviderConnected(mcpStatus) ? (
                        <div className="flex items-center gap-1 text-emerald-400 text-xs font-bold uppercase">
                          <CheckCircle2 size={14} /> Connected
                        </div>
                      ) : (
                        <div className="flex items-center gap-1 text-amber-400 text-xs font-bold uppercase">
                          <AlertCircle size={14} /> {mcpStatus?.status || 'Unknown'}
                        </div>
                      )}
                    </div>
                    <button
                      onClick={refreshSelectedStatus}
                      className="p-2 hover:bg-surface rounded-lg text-gray-400 transition-colors"
                      title="Refresh status"
                    >
                      <RefreshCw size={14} className={isLoadingMcp ? 'animate-spin' : ''} />
                    </button>
                  </div>

                  <p className="text-xs text-gray-400 leading-relaxed">
                    {mcpStatus?.detail || 'No status details available.'}
                  </p>

                  <div className="pt-2 flex gap-3">
                    <button
                      onClick={handleConnectMcp}
                      className="flex-1 bg-accent hover:bg-accent-hover text-gray-900 text-sm font-bold py-2.5 rounded-xl transition-all shadow-md shadow-amber-500/25"
                    >
                      {isProviderConnected(mcpStatus) ? 'Reconnect' : 'Connect Account'}
                    </button>
                    {isProviderConnected(mcpStatus) && (
                      <button
                        onClick={handleDisconnectMcp}
                        title="Forget stored credentials for this MCP"
                        className="px-4 border border-red-500/40 hover:bg-red-500/10 text-red-300 text-sm font-bold rounded-xl transition-all flex items-center gap-2"
                      >
                        <Unplug size={14} />
                        Disconnect
                      </button>
                    )}
                  </div>
                </div>

                {pairingCommand && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <label className="block text-[10px] font-black uppercase text-gray-500 tracking-tighter">Connection Command</label>
                      <button
                        onClick={() => {
                          setPairingCommands((prev) => {
                            const next = { ...prev };
                            delete next[selectedProviderId];
                            writeStoredPairings(next);
                            return next;
                          });
                        }}
                        className="text-[10px] font-bold uppercase text-gray-500 hover:text-gray-300 tracking-wider"
                      >
                        Dismiss
                      </button>
                    </div>
                    <div className="p-4 bg-black rounded-xl border border-border font-mono text-[11px] text-amber-300 break-all overflow-hidden relative group">
                      {pairingCommand}
                      <button
                        onClick={() => navigator.clipboard.writeText(pairingCommand)}
                        className="absolute right-2 top-2 p-1.5 bg-surface rounded-md opacity-0 group-hover:opacity-100 transition-opacity text-amber-300"
                      >
                        <Zap size={12} />
                      </button>
                    </div>
                    <p className="text-[10px] text-gray-500 italic">Run this in your terminal to complete pairing. After it finishes, click the refresh icon above.</p>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'calendar' && (
              <div className="space-y-8">
                <div className="p-6 bg-surface-elevated border border-border rounded-3xl flex items-start gap-5">
                  <div className="w-12 h-12 bg-blue-500/15 rounded-2xl flex items-center justify-center text-blue-400 flex-shrink-0">
                    <Calendar size={24} />
                  </div>
                  <div className="flex-1 space-y-4">
                    <div>
                      <h3 className="font-bold text-white">Google Calendar</h3>
                      <p className="text-xs text-gray-400 mt-1 leading-relaxed">Sync your reminders directly to your Google Calendar.</p>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-black uppercase text-gray-500">Status:</span>
                      {gcalStatus?.connected ? (
                        <span className="text-[10px] font-black uppercase text-emerald-400 flex items-center gap-1">
                          <CheckCircle2 size={12} /> Connected
                        </span>
                      ) : (
                        <span className="text-[10px] font-black uppercase text-gray-500">Not Connected</span>
                      )}
                    </div>

                    <button
                      onClick={handleConnectGcal}
                      className="w-full bg-white text-gray-900 hover:bg-gray-100 text-sm font-bold py-2.5 rounded-xl transition-all flex items-center justify-center gap-2"
                    >
                      {gcalStatus?.connected ? 'Reconnect Google' : 'Connect Google Calendar'}
                      <ExternalLink size={14} />
                    </button>
                  </div>
                </div>

                <div className="p-6 bg-surface-elevated border border-border rounded-3xl flex items-start gap-5">
                  <div className="w-12 h-12 bg-emerald-500/15 rounded-2xl flex items-center justify-center text-emerald-400 flex-shrink-0">
                    <MapPin size={24} />
                  </div>
                  <div className="flex-1 space-y-4 min-w-0">
                    <div>
                      <h3 className="font-bold text-white">Google Maps</h3>
                      <p className="text-xs text-gray-400 mt-1 leading-relaxed">
                        Distance and ETA from your current location. Your browser will ask for location permission the first time you use it.
                      </p>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-black uppercase text-gray-500">Status:</span>
                      {mapsConfigured === null ? (
                        <span className="text-[10px] font-black uppercase text-gray-500">Checking…</span>
                      ) : mapsConfigured ? (
                        <span className="text-[10px] font-black uppercase text-emerald-400 flex items-center gap-1">
                          <CheckCircle2 size={12} /> Ready
                        </span>
                      ) : (
                        <span className="text-[10px] font-black uppercase text-amber-400 flex items-center gap-1">
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
                        className="w-full bg-background border border-border rounded-xl px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-amber-500/50 focus:ring-4 focus:ring-amber-500/10 focus:outline-none disabled:opacity-50"
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
                                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                                : 'border border-border text-gray-400 hover:text-white bg-surface',
                            )}
                          >
                            {mode.label}
                          </button>
                        ))}
                      </div>

                      <button
                        onClick={handleComputeRoute}
                        disabled={!mapsConfigured || mapsLoading || !mapsDestination.trim()}
                        className="w-full bg-emerald-500 hover:bg-emerald-400 text-gray-900 text-sm font-bold py-2.5 rounded-xl transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {mapsLoading ? (
                          <><Loader2 size={14} className="animate-spin" /> Computing…</>
                        ) : (
                          <>Show distance &amp; ETA <MapPin size={14} /></>
                        )}
                      </button>

                      {mapsError && (
                        <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2 leading-relaxed">
                          {mapsError}
                        </div>
                      )}

                      {mapsResult && (
                        <div className="p-4 bg-background border border-emerald-500/30 rounded-2xl space-y-2">
                          <div className="flex items-baseline justify-between">
                            <span className="text-[10px] font-black uppercase text-gray-500">Distance</span>
                            <span className="font-bold text-white">{mapsResult.distance_text}</span>
                          </div>
                          <div className="flex items-baseline justify-between">
                            <span className="text-[10px] font-black uppercase text-gray-500">ETA</span>
                            <span className="font-bold text-emerald-300">{mapsResult.duration_text}</span>
                          </div>
                          {mapsResult.description && (
                            <p className="text-[11px] text-gray-400 italic pt-1 border-t border-border">
                              {mapsResult.description}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <div className="p-6 bg-surface-elevated border border-border rounded-3xl flex items-start gap-5 opacity-60 grayscale">
                  <div className="w-12 h-12 bg-orange-500/15 rounded-2xl flex items-center justify-center text-orange-400 flex-shrink-0">
                    <Zap size={24} />
                  </div>
                  <div className="flex-1 space-y-4">
                    <div>
                      <h3 className="font-bold text-white">Zapier MCP</h3>
                      <p className="text-xs text-gray-400 mt-1 leading-relaxed">Connect to 6,000+ apps via Zapier's MCP server.</p>
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
                  <label className="block text-sm font-bold text-gray-300 mb-4 uppercase tracking-wider">AI Model Provider</label>
                  <div className="space-y-3">
                    {['Auto-detect', 'Gemini', 'OpenAI', 'DeepSeek'].map((provider) => (
                      <button
                        key={provider}
                        className={cn(
                          "w-full p-4 rounded-2xl border text-left transition-all flex items-center justify-between",
                          provider === 'Auto-detect'
                            ? "border-amber-500/60 bg-amber-500/10"
                            : "border-border hover:border-border-strong bg-surface-elevated"
                        )}
                      >
                        <span className="font-bold text-sm text-white">{provider}</span>
                        {provider === 'Auto-detect' && <CheckCircle2 size={16} className="text-amber-400" />}
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
