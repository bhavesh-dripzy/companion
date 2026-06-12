import React, { useEffect, useState } from 'react';
import {
  X,
  Globe,
  Calendar,
  Zap,
  MapPin,
  Car,
  Settings as SettingsIcon,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  RefreshCw,
  Loader2,
  Unplug,
} from 'lucide-react';
import { api } from './api';
import { isNative, runNativeMcpConnect } from './native';
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
  mcpStatuses: Record<string, McpStatus>;
  onMcpStatusesChange: (next: Record<string, McpStatus>) => void;
  isLoadingMcp: boolean;
  onLoadingMcpChange: (loading: boolean) => void;
  gcalStatus: { connected: boolean; configured: boolean } | null;
  onGcalStatusChange: (next: { connected: boolean; configured: boolean } | null) => void;
  mapsConfigured: boolean | null;
  onMapsConfiguredChange: (next: boolean | null) => void;
  initialTab?: 'mcp' | 'calendar' | 'models';
  initialProviderId?: string;
}

function isProviderConnected(status?: McpStatus | null): boolean {
  if (!status) return false;
  const s = status.status;
  if (s === 'ready') return true;
  if (s === 'ready_pending') return true;
  if ((status as any).has_local_tokens || (status as any).has_secret_tokens) return true;
  return false;
}

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
    /* ignore */
  }
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  toolProviders,
  mcpStatuses,
  onMcpStatusesChange,
  isLoadingMcp,
  onLoadingMcpChange,
  gcalStatus,
  onGcalStatusChange,
  mapsConfigured,
  onMapsConfiguredChange,
  initialTab = 'mcp',
  initialProviderId,
}) => {
  const [activeTab, setActiveTab] = useState<'mcp' | 'calendar' | 'models'>(initialTab);
  const [selectedProviderId, setSelectedProviderId] = useState(initialProviderId || 'zomato');
  const [pairingCommands, setPairingCommands] = useState<Record<string, string>>(() =>
    readStoredPairings(),
  );
  const [pasteFlows, setPasteFlows] = useState<Record<string, { url: string; detail: string }>>({});
  const [pasteText, setPasteText] = useState('');
  const [pasteBusy, setPasteBusy] = useState(false);
  const [pasteError, setPasteError] = useState<string | null>(null);

  const [mapsDestination, setMapsDestination] = useState('');
  const [mapsTravelMode, setMapsTravelMode] = useState<MapsTravelMode>('DRIVE');
  const [mapsLoading, setMapsLoading] = useState(false);
  const [mapsResult, setMapsResult] = useState<MapsRouteSummary | null>(null);
  const [mapsError, setMapsError] = useState<string | null>(null);
  const [uberStatus, setUberStatus] = useState<{
    configured: boolean;
    connected: boolean;
    environment?: string;
    production_full_access_required?: boolean;
  } | null>(null);

  const mcpStatus: McpStatus | null = mcpStatuses[selectedProviderId] || null;
  const pairingCommand: string = pairingCommands[selectedProviderId] || '';
  const pasteFlow = pasteFlows[selectedProviderId] || null;

  useEffect(() => {
    setPasteText('');
    setPasteError(null);
  }, [selectedProviderId]);

  useEffect(() => {
    if (isOpen) {
      setActiveTab(initialTab);
      if (initialProviderId) setSelectedProviderId(initialProviderId);
      checkAllMcpStatuses();
      checkGcalStatus();
      checkMapsStatus();
      checkUberStatus();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, initialTab, initialProviderId]);

  const checkAllMcpStatuses = async () => {
    onLoadingMcpChange(true);
    try {
      const data = await api.get('/api/mcp/status/all/');
      const list: McpStatus[] = Array.isArray(data?.statuses) ? data.statuses : [];
      const next: Record<string, McpStatus> = {};
      for (const entry of list) {
        const id = (entry as any).provider;
        if (typeof id === 'string' && id) next[id] = entry;
      }
      onMcpStatusesChange(next);
    } catch (err) {
      console.error('Failed to load MCP statuses', err);
    } finally {
      onLoadingMcpChange(false);
    }
  };

  const refreshSelectedStatus = async () => {
    onLoadingMcpChange(true);
    try {
      const data = await api.get(`/api/mcp/status/?provider=${selectedProviderId}`);
      onMcpStatusesChange({ ...mcpStatuses, [selectedProviderId]: data });
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
      onLoadingMcpChange(false);
    }
  };

  const checkGcalStatus = async () => {
    try {
      const data = await api.get('/api/google-calendar/status/');
      onGcalStatusChange(data);
    } catch (err) {
      console.error('Failed to check Google Calendar status', err);
    }
  };

  const checkMapsStatus = async () => {
    try {
      onMapsConfiguredChange(await mapsStatus());
    } catch (err) {
      console.error('Failed to check Google Maps status', err);
      onMapsConfiguredChange(false);
    }
  };

  const checkUberStatus = async () => {
    try {
      const data = await api.get('/api/uber/status/');
      setUberStatus(data);
    } catch (err) {
      console.error('Failed to check Uber status', err);
      setUberStatus(null);
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
    setPasteError(null);
    // Native app: run the whole OAuth round trip in an intercepting WebView —
    // the localhost redirect is caught automatically, so there is no paste step.
    if (isNative) {
      onLoadingMcpChange(true);
      try {
        await runNativeMcpConnect(selectedProviderId);
        await checkAllMcpStatuses();
      } catch (err: any) {
        if (err?.message !== 'cancelled') {
          setPasteError(err?.message || 'Could not connect. Please try again.');
        }
      } finally {
        onLoadingMcpChange(false);
      }
      return;
    }
    try {
      const data = await api.get(`/api/mcp/oauth/start?provider=${selectedProviderId}`);
      if (data.authorization_url) {
        if (data.completion === 'paste') {
          setPasteFlows((prev) => ({
            ...prev,
            [selectedProviderId]: { url: data.authorization_url, detail: data.detail || '' },
          }));
          window.open(data.authorization_url, '_blank', 'noopener');
        } else if (data.completion === 'external') {
          // Provider login lives entirely on its own site (e.g. Zerodha);
          // status flips to Connected once the user finishes there.
          setPasteError(data.detail || 'Complete the login in the new tab, then tap refresh.');
          window.open(data.authorization_url, '_blank', 'noopener');
        } else {
          window.location.assign(data.authorization_url);
        }
        return;
      }
      if (data.command) {
        recordPairingCommand(selectedProviderId, data.command);
        return;
      }
      if (data.detail) {
        // Nothing actionable came back (not_required / unavailable) — say so
        // instead of silently doing nothing.
        setPasteError(data.detail);
        return;
      }
      const pairing = await api.post(`/api/mcp/pairing/?provider=${selectedProviderId}`);
      if (pairing.command) recordPairingCommand(selectedProviderId, pairing.command);
    } catch (err: any) {
      console.error('Failed to connect MCP', err);
      setPasteError(err?.message || 'Could not start the connection. Please try again.');
    }
  };

  const handlePasteComplete = async () => {
    let text = pasteText.trim();
    if (!text && navigator.clipboard?.readText) {
      try {
        text = (await navigator.clipboard.readText()).trim();
        if (text) setPasteText(text);
      } catch {
        /* clipboard permission denied — user pastes manually */
      }
    }
    if (!text) {
      setPasteError('Paste the full address of the page you landed on after logging in.');
      return;
    }
    setPasteBusy(true);
    setPasteError(null);
    try {
      await api.post('/api/mcp/oauth/complete/', { text });
      setPasteFlows((prev) => {
        const next = { ...prev };
        delete next[selectedProviderId];
        return next;
      });
      setPasteText('');
      await checkAllMcpStatuses();
    } catch (err: any) {
      setPasteError(err?.message || 'Could not finish connecting. Tap Connect to retry.');
    } finally {
      setPasteBusy(false);
    }
  };

  const handleDisconnectMcp = async () => {
    const provider = toolProviders.find((p) => p.id === selectedProviderId);
    const label = provider?.label || selectedProviderId;
    if (!window.confirm(`Disconnect ${label}? Other connected MCPs will stay linked.`)) return;
    onLoadingMcpChange(true);
    try {
      await api.post(`/api/mcp/disconnect/?provider=${selectedProviderId}`);
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
      onLoadingMcpChange(false);
    }
  };

  const handleConnectGcal = async () => {
    try {
      const data = await api.get('/api/google-calendar/oauth/start');
      if (data.authorization_url) window.open(data.authorization_url, '_blank');
    } catch (err) {
      console.error('Failed to connect Google Calendar', err);
    }
  };

  const handleConnectUber = async () => {
    try {
      const data = await api.get('/api/uber/oauth/start');
      if (data.authorization_url) window.open(data.authorization_url, '_blank');
    } catch (err) {
      console.error('Failed to connect Uber', err);
    }
  };

  if (!isOpen) return null;

  const tabs = [
    { id: 'mcp', label: 'MCP Connectors', icon: Globe },
    { id: 'calendar', label: 'Integrations', icon: Calendar },
    { id: 'models', label: 'Preferences', icon: SettingsIcon },
  ] as const;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 md:p-4 pt-[max(0.5rem,var(--safe-top))] pb-[max(0.5rem,var(--safe-bottom))] bg-slate-950/40 backdrop-blur-md">
      <div className="w-full max-w-3xl bg-white/95 border border-slate-200 rounded-2xl md:rounded-3xl shadow-2xl shadow-slate-900/20 flex flex-col max-h-[95vh] md:max-h-[90vh] overflow-hidden">
        <div className="flex items-center justify-between px-5 md:px-8 py-4 md:py-5 border-b border-slate-200">
          <h2 className="text-base md:text-lg font-bold flex items-center gap-2 text-slate-900">
            <SettingsIcon size={18} className="text-slate-700" />
            Settings
          </h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-slate-100 rounded-full transition-colors text-slate-500 hover:text-slate-900"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-col md:flex-row flex-1 overflow-hidden">
          <div className="md:w-52 border-b md:border-b-0 md:border-r border-slate-200 p-2 md:p-4 flex md:flex-col gap-1 md:space-y-1 bg-slate-50/40 overflow-x-auto md:overflow-x-visible shrink-0">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  'flex items-center gap-2 md:gap-3 px-3 py-2 md:py-2.5 rounded-xl text-xs md:text-sm font-semibold transition-all whitespace-nowrap md:w-full',
                  activeTab === tab.id
                    ? 'bg-slate-900 text-white shadow-sm'
                    : 'text-slate-600 hover:text-slate-900 hover:bg-white',
                )}
              >
                <tab.icon size={16} />
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto p-5 md:p-7">
            {activeTab === 'mcp' && (
              <div className="space-y-7">
                <div>
                  <label className="block text-[11px] font-bold text-slate-500 mb-3 uppercase tracking-wider">
                    Select Provider
                  </label>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {toolProviders.map((p) => {
                      const status = mcpStatuses[p.id];
                      const connected = isProviderConnected(status);
                      return (
                        <button
                          key={p.id}
                          onClick={() => setSelectedProviderId(p.id)}
                          className={cn(
                            'p-4 rounded-2xl border text-left transition-all relative',
                            selectedProviderId === p.id
                              ? 'border-slate-900 bg-slate-50 shadow-sm'
                              : 'border-slate-200 hover:border-slate-300 bg-white',
                          )}
                        >
                          <div className="flex items-center justify-between gap-2 mb-1">
                            <div className="font-bold text-sm text-slate-900">{p.label}</div>
                            {connected && (
                              <span className="flex items-center gap-1 text-emerald-600 text-[9px] font-black uppercase tracking-wider">
                                <CheckCircle2 size={10} /> Connected
                              </span>
                            )}
                          </div>
                          <div className="text-[10px] text-slate-500 uppercase font-bold">
                            {!p.available ? 'Setup Required' : connected ? 'Linked' : 'Ready'}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="p-5 bg-slate-50/60 border border-slate-200 rounded-2xl space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-slate-900">Status:</span>
                      {isProviderConnected(mcpStatus) ? (
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
                      onClick={refreshSelectedStatus}
                      className="p-2 hover:bg-white rounded-lg text-slate-500 transition-colors"
                      title="Refresh status"
                    >
                      <RefreshCw size={14} className={isLoadingMcp ? 'animate-spin' : ''} />
                    </button>
                  </div>

                  <p className="text-xs text-slate-600 leading-relaxed">
                    {mcpStatus?.detail || 'No status details available.'}
                  </p>

                  {pasteError && !pasteFlow && (
                    <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 leading-relaxed">
                      {pasteError}
                    </p>
                  )}

                  <div className="pt-1 flex gap-3">
                    <button
                      onClick={handleConnectMcp}
                      className="flex-1 bg-slate-900 hover:bg-slate-800 text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm"
                    >
                      {isProviderConnected(mcpStatus) ? 'Reconnect' : 'Connect Account'}
                    </button>
                    {isProviderConnected(mcpStatus) && (
                      <button
                        onClick={handleDisconnectMcp}
                        title="Forget stored credentials for this MCP"
                        className="px-4 border border-red-200 hover:bg-red-50 text-red-600 text-sm font-bold rounded-xl transition-all flex items-center gap-2"
                      >
                        <Unplug size={14} />
                        Disconnect
                      </button>
                    )}
                  </div>
                </div>

                {pasteFlow && !isProviderConnected(mcpStatus) && (
                  <div className="space-y-3 p-5 bg-blue-50/50 border border-blue-200 rounded-2xl">
                    <div className="flex items-center justify-between">
                      <label className="block text-[10px] font-black uppercase text-blue-700 tracking-wider">
                        Finish Connecting
                      </label>
                      <button
                        onClick={() =>
                          setPasteFlows((prev) => {
                            const next = { ...prev };
                            delete next[selectedProviderId];
                            return next;
                          })
                        }
                        className="text-[10px] font-bold uppercase text-slate-400 hover:text-slate-700 tracking-wider"
                      >
                        Dismiss
                      </button>
                    </div>
                    <a
                      href={pasteFlow.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center justify-center gap-2 w-full bg-blue-600 hover:bg-blue-700 text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm"
                    >
                      <ExternalLink size={14} />
                      Open {toolProviders.find((p) => p.id === selectedProviderId)?.label || 'provider'} login
                    </a>
                    <p className="text-xs text-slate-700 leading-relaxed">
                      After logging in you'll land on a page that can't open — that's expected.
                      Copy that page's full address, come back here, and tap Paste &amp; Connect.
                    </p>
                    <div className="flex gap-2">
                      <input
                        value={pasteText}
                        onChange={(e) => setPasteText(e.target.value)}
                        placeholder="http://localhost:3334/oauth/callback?code=..."
                        className="flex-1 min-w-0 px-3 py-2.5 text-xs font-mono border border-slate-300 rounded-xl bg-white focus:outline-none focus:border-blue-400"
                      />
                      <button
                        onClick={handlePasteComplete}
                        disabled={pasteBusy}
                        className="px-4 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-sm font-bold rounded-xl transition-all flex items-center gap-2 whitespace-nowrap"
                      >
                        {pasteBusy && <Loader2 size={14} className="animate-spin" />}
                        Paste &amp; Connect
                      </button>
                    </div>
                    {pasteError && <p className="text-xs text-red-600">{pasteError}</p>}
                  </div>
                )}

                {pairingCommand && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <label className="block text-[10px] font-black uppercase text-slate-500 tracking-wider">
                        Connection Command
                      </label>
                      <button
                        onClick={() =>
                          setPairingCommands((prev) => {
                            const next = { ...prev };
                            delete next[selectedProviderId];
                            writeStoredPairings(next);
                            return next;
                          })
                        }
                        className="text-[10px] font-bold uppercase text-slate-400 hover:text-slate-700 tracking-wider"
                      >
                        Dismiss
                      </button>
                    </div>
                    <div className="p-4 bg-slate-950 rounded-xl border border-slate-800 font-mono text-[11px] text-amber-300 break-all overflow-hidden relative group">
                      {pairingCommand}
                      <button
                        onClick={() => navigator.clipboard.writeText(pairingCommand)}
                        className="absolute right-2 top-2 p-1.5 bg-slate-800 rounded-md opacity-0 group-hover:opacity-100 transition-opacity text-amber-300"
                      >
                        <Zap size={12} />
                      </button>
                    </div>
                    <p className="text-[10px] text-slate-500 italic">
                      Run this in your terminal to complete pairing. After it finishes, click the refresh icon above.
                    </p>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'calendar' && (
              <div className="space-y-6">
                <div className="p-5 bg-white border border-slate-200 rounded-3xl flex items-start gap-4">
                  <div className="w-11 h-11 bg-blue-50 rounded-2xl flex items-center justify-center text-blue-600 flex-shrink-0">
                    <Calendar size={22} />
                  </div>
                  <div className="flex-1 space-y-3">
                    <div>
                      <h3 className="font-bold text-slate-900">Google Calendar</h3>
                      <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                        Sync your reminders directly to your Google Calendar.
                      </p>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-black uppercase text-slate-500">Status:</span>
                      {gcalStatus?.connected ? (
                        <span className="text-[10px] font-black uppercase text-emerald-600 flex items-center gap-1">
                          <CheckCircle2 size={12} /> Connected
                        </span>
                      ) : (
                        <span className="text-[10px] font-black uppercase text-slate-500">Not Connected</span>
                      )}
                    </div>

                    <button
                      onClick={handleConnectGcal}
                      className="w-full bg-slate-900 text-white hover:bg-slate-800 text-sm font-bold py-2.5 rounded-xl transition-all flex items-center justify-center gap-2 shadow-sm"
                    >
                      {gcalStatus?.connected ? 'Reconnect Google' : 'Connect Google Calendar'}
                      <ExternalLink size={14} />
                    </button>
                  </div>
                </div>

                <div className="p-5 bg-white border border-slate-200 rounded-3xl flex items-start gap-4">
                  <div className="w-11 h-11 bg-slate-100 rounded-2xl flex items-center justify-center text-slate-900 flex-shrink-0">
                    <Car size={22} />
                  </div>
                  <div className="flex-1 space-y-3">
                    <div>
                      <h3 className="font-bold text-slate-900">Uber</h3>
                      <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                        Connect a rider account for cab estimates and ride requests where Uber has approved the required scopes.
                      </p>
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[10px] font-black uppercase text-slate-500">Status:</span>
                      {uberStatus === null ? (
                        <span className="text-[10px] font-black uppercase text-slate-500">Checking…</span>
                      ) : uberStatus.connected ? (
                        <span className="text-[10px] font-black uppercase text-emerald-600 flex items-center gap-1">
                          <CheckCircle2 size={12} /> Connected
                        </span>
                      ) : uberStatus.configured ? (
                        <span className="text-[10px] font-black uppercase text-amber-600 flex items-center gap-1">
                          <AlertCircle size={12} /> Ready to connect
                        </span>
                      ) : (
                        <span className="text-[10px] font-black uppercase text-slate-500">Not configured</span>
                      )}
                      {uberStatus?.environment && (
                        <span className="text-[10px] font-black uppercase text-slate-400">
                          {uberStatus.environment}
                        </span>
                      )}
                    </div>

                    {uberStatus?.production_full_access_required && (
                      <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 leading-relaxed">
                        Production ride requests require Uber approval for the privileged request scope.
                      </div>
                    )}

                    <button
                      onClick={handleConnectUber}
                      disabled={!uberStatus?.configured}
                      className="w-full bg-slate-900 text-white hover:bg-slate-800 text-sm font-bold py-2.5 rounded-xl transition-all flex items-center justify-center gap-2 shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {uberStatus?.connected ? 'Reconnect Uber' : 'Connect Uber'}
                      <ExternalLink size={14} />
                    </button>
                  </div>
                </div>

                <div className="p-5 bg-white border border-slate-200 rounded-3xl flex items-start gap-4">
                  <div className="w-11 h-11 bg-emerald-50 rounded-2xl flex items-center justify-center text-emerald-600 flex-shrink-0">
                    <MapPin size={22} />
                  </div>
                  <div className="flex-1 space-y-3 min-w-0">
                    <div>
                      <h3 className="font-bold text-slate-900">Google Maps</h3>
                      <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                        Distance and ETA from your current location. Your browser will ask for location permission the first time you use it.
                      </p>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-black uppercase text-slate-500">Status:</span>
                      {mapsConfigured === null ? (
                        <span className="text-[10px] font-black uppercase text-slate-500">Checking…</span>
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
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleComputeRoute();
                        }}
                        placeholder="Where to? e.g. Phoenix Mall, Bangalore"
                        disabled={!mapsConfigured || mapsLoading}
                        className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-slate-400 focus:ring-2 focus:ring-slate-900/10 focus:outline-none disabled:opacity-50"
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
                                ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                                : 'border border-slate-200 text-slate-600 hover:text-slate-900 bg-white',
                            )}
                          >
                            {mode.label}
                          </button>
                        ))}
                      </div>

                      <button
                        onClick={handleComputeRoute}
                        disabled={!mapsConfigured || mapsLoading || !mapsDestination.trim()}
                        className="w-full bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-bold py-2.5 rounded-xl transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {mapsLoading ? (
                          <>
                            <Loader2 size={14} className="animate-spin" /> Computing…
                          </>
                        ) : (
                          <>
                            Show distance &amp; ETA <MapPin size={14} />
                          </>
                        )}
                      </button>

                      {mapsError && (
                        <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 leading-relaxed">
                          {mapsError}
                        </div>
                      )}

                      {mapsResult && (
                        <div className="p-4 bg-slate-50 border border-emerald-200 rounded-2xl space-y-2">
                          <div className="flex items-baseline justify-between">
                            <span className="text-[10px] font-black uppercase text-slate-500">Distance</span>
                            <span className="font-bold text-slate-900">{mapsResult.distance_text}</span>
                          </div>
                          <div className="flex items-baseline justify-between">
                            <span className="text-[10px] font-black uppercase text-slate-500">ETA</span>
                            <span className="font-bold text-emerald-700">{mapsResult.duration_text}</span>
                          </div>
                          {mapsResult.description && (
                            <p className="text-[11px] text-slate-500 italic pt-1 border-t border-slate-200">
                              {mapsResult.description}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <div className="p-5 bg-white border border-slate-200 rounded-3xl flex items-start gap-4 opacity-60">
                  <div className="w-11 h-11 bg-orange-50 rounded-2xl flex items-center justify-center text-orange-500 flex-shrink-0">
                    <Zap size={22} />
                  </div>
                  <div className="flex-1 space-y-3">
                    <div>
                      <h3 className="font-bold text-slate-900">Zapier MCP</h3>
                      <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                        Connect to 6,000+ apps via Zapier's MCP server.
                      </p>
                    </div>
                    <button
                      disabled
                      className="w-full border border-slate-200 text-slate-500 text-sm font-bold py-2.5 rounded-xl"
                    >
                      Coming Soon
                    </button>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'models' && (
              <div className="space-y-5">
                <div>
                  <label className="block text-[11px] font-bold text-slate-500 mb-3 uppercase tracking-wider">
                    AI Model Provider
                  </label>
                  <div className="space-y-2">
                    {['Auto-detect', 'Gemini', 'OpenAI', 'DeepSeek'].map((provider) => (
                      <button
                        key={provider}
                        className={cn(
                          'w-full p-4 rounded-2xl border text-left transition-all flex items-center justify-between',
                          provider === 'Auto-detect'
                            ? 'border-slate-900 bg-slate-50 shadow-sm'
                            : 'border-slate-200 hover:border-slate-300 bg-white',
                        )}
                      >
                        <span className="font-bold text-sm text-slate-900">{provider}</span>
                        {provider === 'Auto-detect' && (
                          <CheckCircle2 size={16} className="text-emerald-600" />
                        )}
                      </button>
                    ))}
                  </div>
                </div>

                <p className="text-xs text-slate-500 italic px-1 leading-relaxed">
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
