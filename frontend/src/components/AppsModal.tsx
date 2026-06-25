import React, { useEffect, useState } from 'react';
import {
  X,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  ExternalLink,
  Loader2,
  MapPin,
  ShoppingBag,
  ChevronLeft,
  Plug,
  Unplug,
  ShieldCheck,
} from 'lucide-react';
import { api } from '../api';
import { isNative, runNativeMcpConnect } from '../native';
import { getCurrentLocation, GeolocationError } from '../maps';
import type { McpStatus } from '../types';

// Apps onboarding surface. The auth flow is the SAME backend the rest of the app
// uses (oauth/start → native intercept or paste-back → oauth/complete) — untouched.
// This file is purely the UI: a generic registry of providers drives both a grid
// landing frame (logo + connection status per app) and a shared detail view that
// handles connect / disconnect plus the per-app "source of truth" — a saved
// delivery address (Instamart / Swiggy Food / Zepto) or a live location (District).
// Adding a provider = adding one entry to APPS; no new component.

interface SavedAddress {
  address_id: string;
  location_name: string;
}
interface DefaultAddress {
  address_id: string;
  tag?: string | null;
  line?: string | null;
}
interface DefaultLocation {
  latitude: number;
  longitude: number;
  label?: string | null;
}

// What a provider anchors as its per-user "source of truth".
//  - 'address'  : a saved delivery address id (Instamart / Swiggy Food / Zepto)
//  - 'location' : a live {lat,lng} captured once (District)
type SourceKind = 'address' | 'location';

interface AppConfig {
  key: string; // provider id used by status / oauth / native connect
  label: string;
  short: string; // initials shown in the branded logo tile
  brand: string; // brand hex (logo, accents, paste/cta buttons)
  category: string; // short descriptor shown on the grid tile (e.g. "Grocery delivery")
  source: SourceKind;
  /** address apps: GET/POST endpoint that persists the default address */
  defaultAddressEndpoint?: string;
  /** address apps: validate serviceability on save (Zepto) */
  checkServiceable?: boolean;
  /** location apps: GET/POST endpoint that persists the default location */
  defaultLocationEndpoint?: string;
}

const APPS: AppConfig[] = [
  {
    key: 'swiggy_instamart',
    label: 'Swiggy Instamart',
    short: 'IM',
    brand: '#fc8019',
    category: 'Grocery delivery',
    source: 'address',
    defaultAddressEndpoint: '/api/mcp/instamart/default-address/',
  },
  {
    key: 'swiggy_food',
    label: 'Swiggy Food',
    short: 'SF',
    brand: '#fc8019',
    category: 'Food delivery',
    source: 'address',
    defaultAddressEndpoint: '/api/mcp/swiggy-food/default-address/',
  },
  {
    key: 'zepto',
    label: 'Zepto',
    short: 'Z',
    brand: '#7b2ff7',
    category: 'Grocery delivery',
    source: 'address',
    defaultAddressEndpoint: '/api/mcp/zepto/default-address/',
    checkServiceable: true,
  },
  {
    key: 'district',
    label: 'District',
    short: 'D',
    brand: '#e85d7a',
    category: 'Events & dining',
    source: 'location',
    defaultLocationEndpoint: '/api/mcp/district/default-location/',
  },
];

function isConnected(status?: McpStatus | null): boolean {
  if (!status) return false;
  const s = status.status;
  if (s === 'ready' || s === 'ready_pending') return true;
  return Boolean((status as any).has_local_tokens || (status as any).has_secret_tokens);
}

// 10% alpha tint of a brand hex, for translucent logo/pill backgrounds.
function tint(hex: string): string {
  return `${hex}1a`;
}

// Branded logo tile — generic stand-in for a per-app logo (no logo assets yet).
const AppLogo: React.FC<{ cfg: AppConfig; size?: number }> = ({ cfg, size = 44 }) => (
  <div
    className="rounded-2xl flex items-center justify-center font-extrabold shrink-0"
    style={{
      width: size,
      height: size,
      backgroundColor: tint(cfg.brand),
      color: cfg.brand,
      fontSize: size * 0.34,
    }}
  >
    {cfg.short}
  </div>
);

const StatusPill: React.FC<{ status?: McpStatus | null }> = ({ status }) => {
  const connected = isConnected(status);
  return connected ? (
    <span className="text-[10px] font-black uppercase text-emerald-600 flex items-center gap-1">
      <CheckCircle2 size={12} /> Connected
    </span>
  ) : (
    <span className="text-[10px] font-black uppercase text-amber-600 flex items-center gap-1">
      <AlertCircle size={12} /> {status?.status || 'Not connected'}
    </span>
  );
};

// ---------------------------------------------------------------------------
// Grid landing frame: a tile per app showing its logo + connection status.
// ---------------------------------------------------------------------------
const AppTile: React.FC<{
  cfg: AppConfig;
  status?: McpStatus | null;
  loading: boolean;
  onClick: () => void;
}> = ({ cfg, status, loading, onClick }) => {
  const connected = isConnected(status);
  return (
    <button
      onClick={onClick}
      className="group flex flex-col items-center gap-1.5 p-3 md:p-4 bg-white border border-slate-200 rounded-2xl hover:border-slate-300 hover:shadow-md transition-all text-center"
    >
      <AppLogo cfg={cfg} size={48} />
      <span className="font-sans text-[12px] md:text-sm font-bold text-slate-900 leading-tight line-clamp-2 w-full">
        {cfg.label}
      </span>
      {loading ? (
        <span className="mt-0.5 flex items-center justify-center text-slate-400">
          <Loader2 size={14} className="animate-spin" />
        </span>
      ) : connected ? (
        <span className="mt-0.5 text-[11px] font-bold text-emerald-600 flex items-center gap-1">
          <CheckCircle2 size={13} /> Connected
        </span>
      ) : (
        <>
          <span className="text-[11px] text-slate-400 leading-tight line-clamp-1 w-full">{cfg.category}</span>
          <span
            className="mt-1 w-full text-center text-[12px] font-bold rounded-xl border py-1.5 transition-colors group-hover:bg-slate-50"
            style={{ color: cfg.brand, borderColor: `${cfg.brand}59` }}
          >
            Connect
          </span>
        </>
      )}
    </button>
  );
};

// ---------------------------------------------------------------------------
// Detail view: connect / reconnect + the per-app source of truth. One generic
// component drives every provider; the only branches are address-vs-location and
// the optional serviceability check, both read from the config.
// ---------------------------------------------------------------------------
const AppDetail: React.FC<{
  cfg: AppConfig;
  initialStatus?: McpStatus | null;
  onStatus: (key: string, status: McpStatus | null) => void;
}> = ({ cfg, initialStatus, onStatus }) => {
  const [status, setStatus] = useState<McpStatus | null>(initialStatus ?? null);
  const [busy, setBusy] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pasteFlow, setPasteFlow] = useState<{ url: string; detail: string } | null>(null);
  const [pasteText, setPasteText] = useState('');
  const [pasteBusy, setPasteBusy] = useState(false);
  // address apps
  const [addresses, setAddresses] = useState<SavedAddress[]>([]);
  const [addrLoading, setAddrLoading] = useState(false);
  const [defaultAddr, setDefaultAddr] = useState<DefaultAddress | null>(null);
  const [savingAddr, setSavingAddr] = useState(false);
  const [picking, setPicking] = useState(false);
  // location apps
  const [loc, setLoc] = useState<DefaultLocation | null>(null);
  const [locBusy, setLocBusy] = useState(false);

  const connected = isConnected(status);

  const applyStatus = (data: McpStatus | null) => {
    setStatus(data);
    onStatus(cfg.key, data);
  };

  const refreshStatus = async () => {
    setBusy(true);
    try {
      const data = await api.get(`/api/mcp/status/?provider=${cfg.key}`);
      applyStatus(data);
      return data as McpStatus;
    } catch (err) {
      console.error(`${cfg.key} status check failed`, err);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const loadDefaultAddress = async () => {
    if (cfg.source !== 'address' || !cfg.defaultAddressEndpoint) return;
    try {
      const data = await api.get(cfg.defaultAddressEndpoint);
      setDefaultAddr(data?.default_address || null);
    } catch (err) {
      console.error(`load ${cfg.key} default address failed`, err);
    }
  };

  const loadAddresses = async () => {
    setAddrLoading(true);
    try {
      const data = await api.get(`/api/food/saved-addresses/?provider=${cfg.key}`);
      setAddresses(Array.isArray(data?.addresses) ? data.addresses : []);
    } catch (err: any) {
      console.error(`load ${cfg.key} addresses failed`, err);
      setError(err?.message || 'Could not load your saved addresses.');
    } finally {
      setAddrLoading(false);
    }
  };

  const loadLocation = async () => {
    if (cfg.source !== 'location' || !cfg.defaultLocationEndpoint) return;
    try {
      const data = await api.get(cfg.defaultLocationEndpoint);
      setLoc(data?.default_location || null);
    } catch (err) {
      console.error(`load ${cfg.key} location failed`, err);
    }
  };

  useEffect(() => {
    setError(null);
    setPasteFlow(null);
    setPasteText('');
    (async () => {
      const s = await refreshStatus();
      await loadDefaultAddress();
      await loadLocation();
      if (cfg.source === 'address' && isConnected(s)) await loadAddresses();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg.key]);

  const handleConnect = async () => {
    setError(null);
    if (isNative) {
      setBusy(true);
      try {
        await runNativeMcpConnect(cfg.key);
        const s = await refreshStatus();
        if (cfg.source === 'address' && isConnected(s)) await loadAddresses();
      } catch (err: any) {
        if (err?.message !== 'cancelled') setError(err?.message || 'Could not connect. Please try again.');
      } finally {
        setBusy(false);
      }
      return;
    }
    try {
      const data = await api.get(`/api/mcp/oauth/start?provider=${cfg.key}`);
      if (data.authorization_url) {
        if (data.completion === 'paste') {
          setPasteFlow({ url: data.authorization_url, detail: data.detail || '' });
          window.open(data.authorization_url, '_blank', 'noopener');
        } else if (data.completion === 'external') {
          setError(data.detail || 'Complete the login in the new tab, then tap refresh.');
          window.open(data.authorization_url, '_blank', 'noopener');
        } else {
          window.location.assign(data.authorization_url);
        }
        return;
      }
      if (data.detail) setError(data.detail);
    } catch (err: any) {
      console.error(`${cfg.key} connect failed`, err);
      setError(err?.message || 'Could not start the connection. Please try again.');
    }
  };

  // Forget this provider's stored credentials. Backend wipes only this
  // provider's OAuth files (see /api/mcp/disconnect/); other apps stay paired.
  const handleDisconnect = async () => {
    setError(null);
    setDisconnecting(true);
    try {
      await api.post(`/api/mcp/disconnect/?provider=${cfg.key}`, {});
      // Drop the now-orphaned source-of-truth so a later reconnect starts clean.
      setDefaultAddr(null);
      setLoc(null);
      setAddresses([]);
      setPicking(false);
      setPasteFlow(null);
      setPasteText('');
      await refreshStatus();
    } catch (err: any) {
      setError(err?.message || 'Could not disconnect. Please try again.');
    } finally {
      setDisconnecting(false);
    }
  };

  const handlePasteComplete = async () => {
    let text = pasteText.trim();
    if (!text && navigator.clipboard?.readText) {
      try {
        text = (await navigator.clipboard.readText()).trim();
        if (text) setPasteText(text);
      } catch {
        /* clipboard blocked — manual paste */
      }
    }
    if (!text) {
      setError('Paste the full address of the page you landed on after logging in.');
      return;
    }
    setPasteBusy(true);
    setError(null);
    try {
      await api.post('/api/mcp/oauth/complete/', { text });
      setPasteFlow(null);
      setPasteText('');
      const s = await refreshStatus();
      if (cfg.source === 'address' && isConnected(s)) await loadAddresses();
    } catch (err: any) {
      setError(err?.message || 'Could not finish connecting. Tap Connect to retry.');
    } finally {
      setPasteBusy(false);
    }
  };

  const handleSelectDefault = async (addressId: string) => {
    const row = addresses.find((a) => a.address_id === addressId);
    if (!row || !cfg.defaultAddressEndpoint) return;
    setSavingAddr(true);
    setError(null);
    try {
      const data = await api.post(cfg.defaultAddressEndpoint, {
        address_id: row.address_id,
        tag: row.location_name,
        line: row.location_name,
      });
      if (cfg.checkServiceable && data?.serviceable === false) {
        setError(data?.message || "This app doesn't deliver to this address. Please pick another.");
        return;
      }
      setDefaultAddr(data?.default_address || { address_id: row.address_id, line: row.location_name });
      setPicking(false);
    } catch (err: any) {
      setError(err?.message || 'Could not save the address. Please try again.');
    } finally {
      setSavingAddr(false);
    }
  };

  const handleCaptureLocation = async () => {
    if (!cfg.defaultLocationEndpoint) return;
    setError(null);
    setLocBusy(true);
    try {
      const pos = await getCurrentLocation();
      const data = await api.post(cfg.defaultLocationEndpoint, {
        latitude: pos.latitude,
        longitude: pos.longitude,
        label: 'Current location',
      });
      setLoc(data?.default_location || { latitude: pos.latitude, longitude: pos.longitude });
    } catch (err: any) {
      if (err instanceof GeolocationError) {
        const reason: Record<string, string> = {
          permission_denied: 'Location permission denied. Enable location access for omi and try again.',
          position_unavailable: 'Could not determine your location. Try again outdoors or with GPS on.',
          timeout: 'Locating timed out. Try again.',
          unsupported: 'This device does not support location.',
        };
        setError(reason[err.reason] || 'Could not get your location.');
      } else {
        setError(err?.message || 'Could not save your location. Please try again.');
      }
    } finally {
      setLocBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start gap-3 p-4 md:p-5 bg-white border border-slate-200 rounded-2xl">
        <AppLogo cfg={cfg} />
        <div className="flex-1 min-w-0">
          <h3 className="font-sans font-bold text-slate-900 truncate">{cfg.label}</h3>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-[10px] font-black uppercase text-slate-500">Status:</span>
            <StatusPill status={status} />
          </div>
        </div>
        <button
          onClick={refreshStatus}
          className="p-2 hover:bg-slate-100 rounded-lg text-slate-500 transition-colors shrink-0"
          title="Refresh status"
        >
          <RefreshCw size={14} className={busy ? 'animate-spin' : ''} />
        </button>
      </div>

      {error && !pasteFlow && (
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 leading-relaxed">
          {error}
        </p>
      )}

      {connected ? (
        <button
          onClick={handleDisconnect}
          disabled={disconnecting}
          className="w-full bg-white hover:bg-rose-50 border border-rose-200 text-rose-600 disabled:opacity-60 text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm flex items-center justify-center gap-2"
        >
          {disconnecting ? <Loader2 size={14} className="animate-spin" /> : <Unplug size={14} />}
          Disconnect {cfg.label}
        </button>
      ) : (
        <button
          onClick={handleConnect}
          disabled={busy}
          className="w-full bg-slate-900 hover:bg-slate-800 disabled:opacity-60 text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm flex items-center justify-center gap-2"
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Plug size={14} />}
          Connect {cfg.label}
        </button>
      )}

      {/* Paste-back (web only, when the provider returns paste completion) */}
      {pasteFlow && !connected && (
        <div
          className="space-y-3 p-4 rounded-2xl border"
          style={{ backgroundColor: tint(cfg.brand), borderColor: `${cfg.brand}40` }}
        >
          <a
            href={pasteFlow.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center gap-2 w-full text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm hover:opacity-90"
            style={{ backgroundColor: cfg.brand }}
          >
            <ExternalLink size={14} /> Open {cfg.label} login
          </a>
          <p className="text-xs text-slate-700 leading-relaxed">
            After logging in you'll land on a page that can't open — that's expected. Copy that page's
            full address, come back here, and tap Paste &amp; Connect.
          </p>
          <div className="flex flex-col sm:flex-row gap-2">
            <input
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder="http://localhost:3334/oauth/callback?code=..."
              className="flex-1 min-w-0 px-3 py-2.5 text-xs font-mono border border-slate-300 rounded-xl bg-white focus:outline-none"
            />
            <button
              onClick={handlePasteComplete}
              disabled={pasteBusy}
              className="px-4 py-2.5 sm:py-0 text-white text-sm font-bold rounded-xl transition-all flex items-center justify-center gap-2 whitespace-nowrap disabled:opacity-60 hover:opacity-90 shrink-0"
              style={{ backgroundColor: cfg.brand }}
            >
              {pasteBusy && <Loader2 size={14} className="animate-spin" />}
              Paste &amp; Connect
            </button>
          </div>
          {error && <p className="text-xs text-red-600">{error}</p>}
        </div>
      )}

      {/* Source of truth — saved delivery address (address apps) */}
      {connected && cfg.source === 'address' && (
        <div className="p-4 md:p-5 bg-white border border-slate-200 rounded-2xl space-y-3">
          <div className="flex items-center gap-2">
            <MapPin size={16} style={{ color: cfg.brand }} className="shrink-0" />
            <h3 className="font-sans font-bold text-sm text-slate-900">Default delivery address</h3>
          </div>
          <p className="text-xs text-slate-500 leading-relaxed">
            We'll deliver every {cfg.label} order to this saved address. You can change it anytime.
          </p>

          {defaultAddr && !picking ? (
            <div className="flex items-center justify-between gap-3 p-3 bg-emerald-50/60 border border-emerald-200 rounded-xl">
              <div className="min-w-0">
                <div className="text-xs font-bold text-slate-900 truncate">
                  {defaultAddr.tag || defaultAddr.line || defaultAddr.address_id}
                </div>
                {defaultAddr.line && defaultAddr.line !== defaultAddr.tag && (
                  <div className="text-[11px] text-slate-500 truncate">{defaultAddr.line}</div>
                )}
              </div>
              <button
                onClick={() => {
                  setPicking(true);
                  if (addresses.length === 0) loadAddresses();
                }}
                className="text-[11px] font-bold uppercase tracking-wider shrink-0 hover:opacity-80"
                style={{ color: cfg.brand }}
              >
                Change
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              {addrLoading ? (
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <Loader2 size={14} className="animate-spin" /> Loading your saved addresses…
                </div>
              ) : addresses.length === 0 ? (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                  No saved {cfg.label} addresses found. Add one in the {cfg.label} app, then refresh.
                </p>
              ) : (
                <select
                  defaultValue={defaultAddr?.address_id || ''}
                  disabled={savingAddr}
                  onChange={(e) => e.target.value && handleSelectDefault(e.target.value)}
                  className="w-full px-3 py-2.5 text-sm border border-slate-300 rounded-xl bg-white focus:outline-none disabled:opacity-60"
                >
                  <option value="" disabled>
                    Select a delivery address…
                  </option>
                  {addresses.map((a) => (
                    <option key={a.address_id} value={a.address_id}>
                      {a.location_name}
                    </option>
                  ))}
                </select>
              )}
              {savingAddr && (
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <Loader2 size={14} className="animate-spin" /> Saving…
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Source of truth — discovery location (location apps) */}
      {connected && cfg.source === 'location' && (
        <div className="p-4 md:p-5 bg-white border border-slate-200 rounded-2xl space-y-3">
          <div className="flex items-center gap-2">
            <MapPin size={16} style={{ color: cfg.brand }} className="shrink-0" />
            <h3 className="font-sans font-bold text-sm text-slate-900">Discovery location</h3>
          </div>
          <p className="text-xs text-slate-500 leading-relaxed">
            {cfg.label} shows movies, events and dining near you. We save your location once and reuse
            it for every search — update it any time you move.
          </p>
          {loc ? (
            <div className="flex items-center justify-between gap-3 p-3 bg-emerald-50/60 border border-emerald-200 rounded-xl">
              <div className="min-w-0">
                <div className="text-xs font-bold text-slate-900 truncate">{loc.label || 'Saved location'}</div>
                <div className="text-[11px] text-slate-500 truncate">
                  {loc.latitude.toFixed(5)}, {loc.longitude.toFixed(5)}
                </div>
              </div>
              <button
                onClick={handleCaptureLocation}
                disabled={locBusy}
                className="text-[11px] font-bold uppercase tracking-wider shrink-0 flex items-center gap-1 hover:opacity-80"
                style={{ color: cfg.brand }}
              >
                {locBusy && <Loader2 size={12} className="animate-spin" />} Update
              </button>
            </div>
          ) : (
            <button
              onClick={handleCaptureLocation}
              disabled={locBusy}
              className="w-full text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm flex items-center justify-center gap-2 disabled:opacity-60 hover:opacity-90"
              style={{ backgroundColor: cfg.brand }}
            >
              {locBusy ? <Loader2 size={14} className="animate-spin" /> : <MapPin size={14} />}
              Use my current location
            </button>
          )}
        </div>
      )}
    </div>
  );
};

interface AppsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const AppsModal: React.FC<AppsModalProps> = ({ isOpen, onClose }) => {
  // null = grid landing frame; otherwise the selected provider key.
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [statuses, setStatuses] = useState<Record<string, McpStatus | null>>({});
  const [gridLoading, setGridLoading] = useState(false);

  const selected = selectedKey ? APPS.find((a) => a.key === selectedKey) || null : null;

  // Fetch every provider's status up-front so the grid tiles show connected/not.
  const loadAllStatuses = async () => {
    setGridLoading(true);
    try {
      const results = await Promise.all(
        APPS.map(async (cfg) => {
          try {
            const data = await api.get(`/api/mcp/status/?provider=${cfg.key}`);
            return [cfg.key, data as McpStatus] as const;
          } catch {
            return [cfg.key, null] as const;
          }
        }),
      );
      setStatuses(Object.fromEntries(results));
    } finally {
      setGridLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    setSelectedKey(null);
    loadAllStatuses();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const handleStatus = (key: string, status: McpStatus | null) => {
    setStatuses((prev) => ({ ...prev, [key]: status }));
  };

  if (!isOpen) return null;

  // Split providers by connection status for the grid's two sections. While the
  // initial status sweep is in flight we don't yet know which is which, so we
  // show every tile in one neutral block (each with its own spinner).
  const connectedApps = APPS.filter((cfg) => isConnected(statuses[cfg.key]));
  const otherApps = APPS.filter((cfg) => !isConnected(statuses[cfg.key]));

  const renderTile = (cfg: AppConfig) => (
    <AppTile
      key={cfg.key}
      cfg={cfg}
      status={statuses[cfg.key]}
      loading={gridLoading && statuses[cfg.key] === undefined}
      onClick={() => setSelectedKey(cfg.key)}
    />
  );

  const renderSection = (title: string, apps: AppConfig[]) =>
    apps.length === 0 ? null : (
      <div className="space-y-3">
        <h3 className="font-sans text-xs font-bold uppercase tracking-wider text-slate-500">{title}</h3>
        <div className="grid grid-cols-3 gap-2.5 md:gap-3">{apps.map(renderTile)}</div>
      </div>
    );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 md:p-4 pt-[max(0.5rem,var(--safe-top))] pb-[max(0.5rem,var(--safe-bottom))] bg-slate-950/40 backdrop-blur-md">
      <div className="w-full max-w-lg bg-white/95 border border-slate-200 rounded-2xl md:rounded-3xl shadow-2xl shadow-slate-900/20 flex flex-col max-h-[95vh] md:max-h-[90vh] overflow-hidden">
        <div className="px-4 md:px-7 py-4 md:py-5 border-b border-slate-200">
          <div className="flex items-center justify-between gap-2">
            <h2 className="font-sans text-base md:text-lg font-bold flex items-center gap-2 text-slate-900 min-w-0">
              {selected ? (
                <button
                  onClick={() => setSelectedKey(null)}
                  className="p-1.5 -ml-1.5 hover:bg-slate-100 rounded-full transition-colors text-slate-500 hover:text-slate-900 shrink-0"
                  title="Back to apps"
                >
                  <ChevronLeft size={18} />
                </button>
              ) : (
                <ShoppingBag size={18} className="text-[#fc8019] shrink-0" />
              )}
              <span className="truncate">{selected ? selected.label : 'Apps & Connections'}</span>
            </h2>
            <button
              onClick={onClose}
              className="p-2 hover:bg-slate-100 rounded-full transition-colors text-slate-500 hover:text-slate-900 shrink-0"
            >
              <X size={18} />
            </button>
          </div>
          {!selected && (
            <p className="font-sans text-xs md:text-sm text-slate-500 mt-1 leading-relaxed">
              Connect your favourite apps to get things done seamlessly.
            </p>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-4 md:p-7">
          {selected ? (
            <AppDetail cfg={selected} initialStatus={statuses[selected.key]} onStatus={handleStatus} />
          ) : gridLoading && connectedApps.length === 0 ? (
            <div className="grid grid-cols-3 gap-2.5 md:gap-3">{APPS.map(renderTile)}</div>
          ) : (
            <div className="space-y-6">
              {renderSection('Connected apps', connectedApps)}
              {renderSection('Explore more apps', otherApps)}
            </div>
          )}
        </div>

        {!selected && (
          <div className="px-4 md:px-7 py-3 border-t border-slate-200 flex items-center justify-center gap-2 text-[11px] md:text-xs text-slate-400">
            <ShieldCheck size={14} className="shrink-0" />
            Your data is secure and private. We never share your information.
          </div>
        )}
      </div>
    </div>
  );
};
