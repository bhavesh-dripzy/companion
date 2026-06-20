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
} from 'lucide-react';
import { api } from '../api';
import { isNative, runNativeMcpConnect } from '../native';
import { getCurrentLocation, GeolocationError } from '../maps';
import type { McpStatus } from '../types';

// Apps onboarding surface. Scoped to Swiggy Instamart for now. The auth flow is
// the SAME backend the rest of the app uses (oauth/start → native intercept or
// paste-back → oauth/complete) — untouched. What's new here is the per-user
// default delivery address: after connecting, the user picks a saved Swiggy
// address and we persist it as the source of truth via
// /api/mcp/instamart/default-address/.

const PROVIDER = 'swiggy_instamart';
const LABEL = 'Swiggy Instamart';

interface SavedAddress {
  address_id: string;
  location_name: string;
}
interface DefaultAddress {
  address_id: string;
  tag?: string | null;
  line?: string | null;
}

function isConnected(status?: McpStatus | null): boolean {
  if (!status) return false;
  const s = status.status;
  if (s === 'ready' || s === 'ready_pending') return true;
  return Boolean((status as any).has_local_tokens || (status as any).has_secret_tokens);
}

interface DefaultLocation {
  latitude: number;
  longitude: number;
  label?: string | null;
}

// Zepto onboarding. Same auth backend as everything else (provider='zepto').
// The per-user "source of truth" is a SAVED DELIVERY ADDRESS (mirrors Swiggy
// Instamart): select_saved_address binds BOTH the store/serviceability AND the
// order doorstep from one address id. After connecting, the user picks a saved
// Zepto address; we persist it via /api/mcp/zepto/default-address/ and the
// executor pins it every turn. (GPS remains a backend-only fallback.)
const ZeptoApp: React.FC<{ isOpen: boolean }> = ({ isOpen }) => {
  const [status, setStatus] = useState<McpStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pasteFlow, setPasteFlow] = useState<{ url: string; detail: string } | null>(null);
  const [pasteText, setPasteText] = useState('');
  const [pasteBusy, setPasteBusy] = useState(false);
  const [addresses, setAddresses] = useState<SavedAddress[]>([]);
  const [addrLoading, setAddrLoading] = useState(false);
  const [defaultAddr, setDefaultAddr] = useState<DefaultAddress | null>(null);
  const [savingAddr, setSavingAddr] = useState(false);
  const [picking, setPicking] = useState(false);
  const connected = isConnected(status);

  const refreshStatus = async () => {
    setBusy(true);
    try {
      const data = await api.get(`/api/mcp/status/?provider=zepto`);
      setStatus(data);
      return data as McpStatus;
    } catch {
      return null;
    } finally {
      setBusy(false);
    }
  };

  const loadDefaultAddress = async () => {
    try {
      const data = await api.get('/api/mcp/zepto/default-address/');
      setDefaultAddr(data?.default_address || null);
    } catch (err) {
      console.error('load zepto default address failed', err);
    }
  };

  const loadAddresses = async () => {
    setAddrLoading(true);
    try {
      const data = await api.get('/api/food/saved-addresses/?provider=zepto');
      setAddresses(Array.isArray(data?.addresses) ? data.addresses : []);
    } catch (err: any) {
      console.error('load zepto addresses failed', err);
      setError(err?.message || 'Could not load your Zepto addresses.');
    } finally {
      setAddrLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setPasteFlow(null);
    setPasteText('');
    (async () => {
      await refreshStatus();
      await loadDefaultAddress();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // Pull the saved-address list once the provider is connected (covers both
  // open-when-connected and connect-now, without touching the auth handlers).
  useEffect(() => {
    if (isOpen && connected) loadAddresses();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, connected]);

  const handleConnect = async () => {
    setError(null);
    if (isNative) {
      setBusy(true);
      try {
        await runNativeMcpConnect('zepto');
        await refreshStatus();
      } catch (err: any) {
        if (err?.message !== 'cancelled') setError(err?.message || 'Could not connect. Please try again.');
      } finally {
        setBusy(false);
      }
      return;
    }
    try {
      const data = await api.get(`/api/mcp/oauth/start?provider=zepto`);
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
      setError(err?.message || 'Could not start the connection. Please try again.');
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
      await refreshStatus();
    } catch (err: any) {
      setError(err?.message || 'Could not finish connecting. Tap Connect to retry.');
    } finally {
      setPasteBusy(false);
    }
  };

  const handleSelectDefault = async (addressId: string) => {
    const row = addresses.find((a) => a.address_id === addressId);
    if (!row) return;
    setSavingAddr(true);
    setError(null);
    try {
      const data = await api.post('/api/mcp/zepto/default-address/', {
        address_id: row.address_id,
        tag: row.location_name,
        line: row.location_name,
      });
      if (data?.serviceable === false) {
        setError(data?.message || "Zepto doesn't deliver to this address. Please pick another.");
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

  return (
    <div className="p-5 bg-white border border-slate-200 rounded-2xl space-y-4">
      <div className="flex items-start gap-3">
        <div className="w-11 h-11 rounded-2xl bg-[#7b2ff7]/10 text-[#7b2ff7] flex items-center justify-center font-extrabold shrink-0">
          Z
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-bold text-slate-900">Zepto</h3>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-[10px] font-black uppercase text-slate-500">Status:</span>
            {connected ? (
              <span className="text-[10px] font-black uppercase text-emerald-600 flex items-center gap-1">
                <CheckCircle2 size={12} /> Connected
              </span>
            ) : (
              <span className="text-[10px] font-black uppercase text-amber-600 flex items-center gap-1">
                <AlertCircle size={12} /> {status?.status || 'Not connected'}
              </span>
            )}
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

      <button
        onClick={handleConnect}
        disabled={busy}
        className="w-full bg-slate-900 hover:bg-slate-800 disabled:opacity-60 text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm flex items-center justify-center gap-2"
      >
        {busy && <Loader2 size={14} className="animate-spin" />}
        {connected ? 'Reconnect Zepto' : 'Connect Zepto'}
      </button>

      {pasteFlow && !connected && (
        <div className="space-y-3 p-4 bg-violet-50/60 border border-violet-200 rounded-2xl">
          <a
            href={pasteFlow.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center gap-2 w-full bg-violet-600 hover:bg-violet-700 text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm"
          >
            <ExternalLink size={14} /> Open Zepto login
          </a>
          <p className="text-xs text-slate-700 leading-relaxed">
            After logging in, copy the page's full address, come back here, and tap Paste &amp; Connect.
          </p>
          <div className="flex gap-2">
            <input
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder="http://localhost:3334/oauth/callback?code=..."
              className="flex-1 min-w-0 px-3 py-2.5 text-xs font-mono border border-slate-300 rounded-xl bg-white focus:outline-none focus:border-violet-400"
            />
            <button
              onClick={handlePasteComplete}
              disabled={pasteBusy}
              className="px-4 bg-violet-600 hover:bg-violet-700 disabled:opacity-60 text-white text-sm font-bold rounded-xl transition-all flex items-center gap-2 whitespace-nowrap"
            >
              {pasteBusy && <Loader2 size={14} className="animate-spin" />}
              Paste &amp; Connect
            </button>
          </div>
        </div>
      )}

      {/* Default delivery address (the Zepto source of truth — mirrors Instamart) */}
      {connected && (
        <div className="pt-1 space-y-3">
          <div className="flex items-center gap-2">
            <MapPin size={16} className="text-[#7b2ff7]" />
            <h3 className="font-bold text-sm text-slate-900">Default delivery address</h3>
          </div>
          <p className="text-xs text-slate-500 leading-relaxed">
            We'll deliver every Zepto order to this saved address. You can change it anytime.
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
                className="text-[11px] font-bold uppercase text-[#7b2ff7] hover:text-[#5a1fb8] tracking-wider shrink-0"
              >
                Change
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              {addrLoading ? (
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <Loader2 size={14} className="animate-spin" /> Loading your Zepto addresses…
                </div>
              ) : addresses.length === 0 ? (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                  No saved Zepto addresses found. Add one in the Zepto app, then refresh.
                </p>
              ) : (
                <select
                  defaultValue={defaultAddr?.address_id || ''}
                  disabled={savingAddr}
                  onChange={(e) => e.target.value && handleSelectDefault(e.target.value)}
                  className="w-full px-3 py-2.5 text-sm border border-slate-300 rounded-xl bg-white focus:outline-none focus:border-[#7b2ff7] disabled:opacity-60"
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
    </div>
  );
};

// Swiggy Food onboarding. Same auth backend as everything else (provider='swiggy_food').
// The per-user "source of truth" is a SAVED DELIVERY ADDRESS (mirrors Swiggy
// Instamart / Zepto): Swiggy Food's search / menu / cart / order tools all require
// an addressId, so we pin one. After connecting, the user picks a saved Swiggy
// address; we persist it via /api/mcp/swiggy-food/default-address/ and the executor
// anchors it every turn. (Serviceability on Swiggy is per-restaurant, so there's
// nothing to validate at save time.)
const SwiggyFoodApp: React.FC<{ isOpen: boolean }> = ({ isOpen }) => {
  const [status, setStatus] = useState<McpStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pasteFlow, setPasteFlow] = useState<{ url: string; detail: string } | null>(null);
  const [pasteText, setPasteText] = useState('');
  const [pasteBusy, setPasteBusy] = useState(false);
  const [addresses, setAddresses] = useState<SavedAddress[]>([]);
  const [addrLoading, setAddrLoading] = useState(false);
  const [defaultAddr, setDefaultAddr] = useState<DefaultAddress | null>(null);
  const [savingAddr, setSavingAddr] = useState(false);
  const [picking, setPicking] = useState(false);
  const connected = isConnected(status);

  const refreshStatus = async () => {
    setBusy(true);
    try {
      const data = await api.get(`/api/mcp/status/?provider=swiggy_food`);
      setStatus(data);
      return data as McpStatus;
    } catch {
      return null;
    } finally {
      setBusy(false);
    }
  };

  const loadDefaultAddress = async () => {
    try {
      const data = await api.get('/api/mcp/swiggy-food/default-address/');
      setDefaultAddr(data?.default_address || null);
    } catch (err) {
      console.error('load swiggy food default address failed', err);
    }
  };

  const loadAddresses = async () => {
    setAddrLoading(true);
    try {
      const data = await api.get('/api/food/saved-addresses/?provider=swiggy_food');
      setAddresses(Array.isArray(data?.addresses) ? data.addresses : []);
    } catch (err: any) {
      console.error('load swiggy food addresses failed', err);
      setError(err?.message || 'Could not load your Swiggy addresses.');
    } finally {
      setAddrLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setPasteFlow(null);
    setPasteText('');
    (async () => {
      await refreshStatus();
      await loadDefaultAddress();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  useEffect(() => {
    if (isOpen && connected) loadAddresses();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, connected]);

  const handleConnect = async () => {
    setError(null);
    if (isNative) {
      setBusy(true);
      try {
        await runNativeMcpConnect('swiggy_food');
        await refreshStatus();
      } catch (err: any) {
        if (err?.message !== 'cancelled') setError(err?.message || 'Could not connect. Please try again.');
      } finally {
        setBusy(false);
      }
      return;
    }
    try {
      const data = await api.get(`/api/mcp/oauth/start?provider=swiggy_food`);
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
      setError(err?.message || 'Could not start the connection. Please try again.');
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
      await refreshStatus();
    } catch (err: any) {
      setError(err?.message || 'Could not finish connecting. Tap Connect to retry.');
    } finally {
      setPasteBusy(false);
    }
  };

  const handleSelectDefault = async (addressId: string) => {
    const row = addresses.find((a) => a.address_id === addressId);
    if (!row) return;
    setSavingAddr(true);
    setError(null);
    try {
      const data = await api.post('/api/mcp/swiggy-food/default-address/', {
        address_id: row.address_id,
        tag: row.location_name,
        line: row.location_name,
      });
      setDefaultAddr(data?.default_address || { address_id: row.address_id, line: row.location_name });
      setPicking(false);
    } catch (err: any) {
      setError(err?.message || 'Could not save the address. Please try again.');
    } finally {
      setSavingAddr(false);
    }
  };

  return (
    <div className="p-5 bg-white border border-slate-200 rounded-2xl space-y-4">
      <div className="flex items-start gap-3">
        <div className="w-11 h-11 rounded-2xl bg-[#fc8019]/10 text-[#fc8019] flex items-center justify-center font-extrabold shrink-0">
          S
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-bold text-slate-900">Swiggy Food</h3>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-[10px] font-black uppercase text-slate-500">Status:</span>
            {connected ? (
              <span className="text-[10px] font-black uppercase text-emerald-600 flex items-center gap-1">
                <CheckCircle2 size={12} /> Connected
              </span>
            ) : (
              <span className="text-[10px] font-black uppercase text-amber-600 flex items-center gap-1">
                <AlertCircle size={12} /> {status?.status || 'Not connected'}
              </span>
            )}
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

      <button
        onClick={handleConnect}
        disabled={busy}
        className="w-full bg-slate-900 hover:bg-slate-800 disabled:opacity-60 text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm flex items-center justify-center gap-2"
      >
        {busy && <Loader2 size={14} className="animate-spin" />}
        {connected ? 'Reconnect Swiggy Food' : 'Connect Swiggy Food'}
      </button>

      {pasteFlow && !connected && (
        <div className="space-y-3 p-4 bg-orange-50/60 border border-orange-200 rounded-2xl">
          <a
            href={pasteFlow.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center gap-2 w-full bg-[#fc8019] hover:bg-[#e0710f] text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm"
          >
            <ExternalLink size={14} /> Open Swiggy login
          </a>
          <p className="text-xs text-slate-700 leading-relaxed">
            After logging in, copy the page's full address, come back here, and tap Paste &amp; Connect.
          </p>
          <div className="flex gap-2">
            <input
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder="http://localhost:3334/oauth/callback?code=..."
              className="flex-1 min-w-0 px-3 py-2.5 text-xs font-mono border border-slate-300 rounded-xl bg-white focus:outline-none focus:border-orange-400"
            />
            <button
              onClick={handlePasteComplete}
              disabled={pasteBusy}
              className="px-4 bg-[#fc8019] hover:bg-[#e0710f] disabled:opacity-60 text-white text-sm font-bold rounded-xl transition-all flex items-center gap-2 whitespace-nowrap"
            >
              {pasteBusy && <Loader2 size={14} className="animate-spin" />}
              Paste &amp; Connect
            </button>
          </div>
        </div>
      )}

      {/* Default delivery address (the Swiggy Food source of truth — mirrors Instamart) */}
      {connected && (
        <div className="pt-1 space-y-3">
          <div className="flex items-center gap-2">
            <MapPin size={16} className="text-[#fc8019]" />
            <h3 className="font-bold text-sm text-slate-900">Default delivery address</h3>
          </div>
          <p className="text-xs text-slate-500 leading-relaxed">
            We'll deliver every Swiggy Food order to this saved address. You can change it anytime.
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
                className="text-[11px] font-bold uppercase text-[#fc8019] hover:text-[#e0710f] tracking-wider shrink-0"
              >
                Change
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              {addrLoading ? (
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <Loader2 size={14} className="animate-spin" /> Loading your Swiggy addresses…
                </div>
              ) : addresses.length === 0 ? (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                  No saved Swiggy addresses found. Add one in the Swiggy app, then refresh.
                </p>
              ) : (
                <select
                  defaultValue={defaultAddr?.address_id || ''}
                  disabled={savingAddr}
                  onChange={(e) => e.target.value && handleSelectDefault(e.target.value)}
                  className="w-full px-3 py-2.5 text-sm border border-slate-300 rounded-xl bg-white focus:outline-none focus:border-[#fc8019] disabled:opacity-60"
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
    </div>
  );
};

// District onboarding. Same auth backend as everything else (provider='district'),
// and like Zepto its source of truth is a LIVE LOCATION, not a saved address —
// District search tools take {lat,lng} and have no saved-address concept. We ask
// for the location permission once during onboarding, persist {lat,lng} via
// /api/mcp/district/default-location/, and reuse it every turn (the user can
// re-capture it any time). Mirrors the Zepto card's connect + location flow.
const DistrictApp: React.FC<{ isOpen: boolean }> = ({ isOpen }) => {
  const [status, setStatus] = useState<McpStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pasteFlow, setPasteFlow] = useState<{ url: string; detail: string } | null>(null);
  const [pasteText, setPasteText] = useState('');
  const [pasteBusy, setPasteBusy] = useState(false);
  const [loc, setLoc] = useState<DefaultLocation | null>(null);
  const [locBusy, setLocBusy] = useState(false);
  const connected = isConnected(status);

  const refreshStatus = async () => {
    setBusy(true);
    try {
      const data = await api.get(`/api/mcp/status/?provider=district`);
      setStatus(data);
      return data as McpStatus;
    } catch {
      return null;
    } finally {
      setBusy(false);
    }
  };

  const loadLocation = async () => {
    try {
      const data = await api.get('/api/mcp/district/default-location/');
      setLoc(data?.default_location || null);
    } catch (err) {
      console.error('load district location failed', err);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setPasteFlow(null);
    setPasteText('');
    (async () => {
      await refreshStatus();
      await loadLocation();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const handleConnect = async () => {
    setError(null);
    if (isNative) {
      setBusy(true);
      try {
        await runNativeMcpConnect('district');
        await refreshStatus();
      } catch (err: any) {
        if (err?.message !== 'cancelled') setError(err?.message || 'Could not connect. Please try again.');
      } finally {
        setBusy(false);
      }
      return;
    }
    try {
      const data = await api.get(`/api/mcp/oauth/start?provider=district`);
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
      setError(err?.message || 'Could not start the connection. Please try again.');
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
      await refreshStatus();
    } catch (err: any) {
      setError(err?.message || 'Could not finish connecting. Tap Connect to retry.');
    } finally {
      setPasteBusy(false);
    }
  };

  const handleCaptureLocation = async () => {
    setError(null);
    setLocBusy(true);
    try {
      const pos = await getCurrentLocation();
      const data = await api.post('/api/mcp/district/default-location/', {
        latitude: pos.latitude,
        longitude: pos.longitude,
        label: 'Current location',
      });
      setLoc(data?.default_location || { latitude: pos.latitude, longitude: pos.longitude });
    } catch (err: any) {
      if (err instanceof GeolocationError) {
        const reason: Record<string, string> = {
          permission_denied: 'Location permission denied. Enable location access for Companion and try again.',
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
    <div className="p-5 bg-white border border-slate-200 rounded-2xl space-y-4">
      <div className="flex items-start gap-3">
        <div className="w-11 h-11 rounded-2xl bg-[#e85d7a]/10 text-[#c23e5b] flex items-center justify-center font-extrabold shrink-0">
          D
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-bold text-slate-900">District</h3>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-[10px] font-black uppercase text-slate-500">Status:</span>
            {connected ? (
              <span className="text-[10px] font-black uppercase text-emerald-600 flex items-center gap-1">
                <CheckCircle2 size={12} /> Connected
              </span>
            ) : (
              <span className="text-[10px] font-black uppercase text-amber-600 flex items-center gap-1">
                <AlertCircle size={12} /> {status?.status || 'Not connected'}
              </span>
            )}
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

      <button
        onClick={handleConnect}
        disabled={busy}
        className="w-full bg-slate-900 hover:bg-slate-800 disabled:opacity-60 text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm flex items-center justify-center gap-2"
      >
        {busy && <Loader2 size={14} className="animate-spin" />}
        {connected ? 'Reconnect District' : 'Connect District'}
      </button>

      {pasteFlow && !connected && (
        <div className="space-y-3 p-4 bg-rose-50/60 border border-rose-200 rounded-2xl">
          <a
            href={pasteFlow.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center gap-2 w-full bg-[#e85d7a] hover:bg-[#c23e5b] text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm"
          >
            <ExternalLink size={14} /> Open District login
          </a>
          <p className="text-xs text-slate-700 leading-relaxed">
            After logging in, copy the page's full address, come back here, and tap Paste &amp; Connect.
          </p>
          <div className="flex gap-2">
            <input
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder="http://localhost:3334/oauth/callback?code=..."
              className="flex-1 min-w-0 px-3 py-2.5 text-xs font-mono border border-slate-300 rounded-xl bg-white focus:outline-none focus:border-rose-400"
            />
            <button
              onClick={handlePasteComplete}
              disabled={pasteBusy}
              className="px-4 bg-[#e85d7a] hover:bg-[#c23e5b] disabled:opacity-60 text-white text-sm font-bold rounded-xl transition-all flex items-center gap-2 whitespace-nowrap"
            >
              {pasteBusy && <Loader2 size={14} className="animate-spin" />}
              Paste &amp; Connect
            </button>
          </div>
        </div>
      )}

      {/* Discovery location (the District source of truth) */}
      {connected && (
        <div className="pt-1 space-y-3">
          <div className="flex items-center gap-2">
            <MapPin size={16} className="text-[#e85d7a]" />
            <h3 className="font-bold text-sm text-slate-900">Discovery location</h3>
          </div>
          <p className="text-xs text-slate-500 leading-relaxed">
            District shows movies, events and dining near you. We save your location once and reuse
            it for every search — update it any time you move.
          </p>
          {loc ? (
            <div className="flex items-center justify-between gap-3 p-3 bg-emerald-50/60 border border-emerald-200 rounded-xl">
              <div className="min-w-0">
                <div className="text-xs font-bold text-slate-900 truncate">
                  {loc.label || 'Saved location'}
                </div>
                <div className="text-[11px] text-slate-500 truncate">
                  {loc.latitude.toFixed(5)}, {loc.longitude.toFixed(5)}
                </div>
              </div>
              <button
                onClick={handleCaptureLocation}
                disabled={locBusy}
                className="text-[11px] font-bold uppercase text-[#c23e5b] hover:text-[#e85d7a] tracking-wider shrink-0 flex items-center gap-1"
              >
                {locBusy && <Loader2 size={12} className="animate-spin" />} Update
              </button>
            </div>
          ) : (
            <button
              onClick={handleCaptureLocation}
              disabled={locBusy}
              className="w-full bg-[#e85d7a] hover:bg-[#c23e5b] disabled:opacity-60 text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm flex items-center justify-center gap-2"
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
  const [status, setStatus] = useState<McpStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Web paste-back flow (Swiggy whitelists only localhost, so on web the user
  // copies the post-login URL back here; on the APK it's auto-intercepted).
  const [pasteFlow, setPasteFlow] = useState<{ url: string; detail: string } | null>(null);
  const [pasteText, setPasteText] = useState('');
  const [pasteBusy, setPasteBusy] = useState(false);

  const [addresses, setAddresses] = useState<SavedAddress[]>([]);
  const [addrLoading, setAddrLoading] = useState(false);
  const [defaultAddr, setDefaultAddr] = useState<DefaultAddress | null>(null);
  const [savingAddr, setSavingAddr] = useState(false);
  const [picking, setPicking] = useState(false);

  const connected = isConnected(status);

  const refreshStatus = async () => {
    setBusy(true);
    try {
      const data = await api.get(`/api/mcp/status/?provider=${PROVIDER}`);
      setStatus(data);
      return data as McpStatus;
    } catch (err) {
      console.error('Swiggy status check failed', err);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const loadDefaultAddress = async () => {
    try {
      const data = await api.get('/api/mcp/instamart/default-address/');
      setDefaultAddr(data?.default_address || null);
    } catch (err) {
      console.error('load default address failed', err);
    }
  };

  const loadAddresses = async () => {
    setAddrLoading(true);
    try {
      const data = await api.get(`/api/food/saved-addresses/?provider=${PROVIDER}`);
      setAddresses(Array.isArray(data?.addresses) ? data.addresses : []);
    } catch (err: any) {
      console.error('load addresses failed', err);
      setError(err?.message || 'Could not load your Swiggy addresses.');
    } finally {
      setAddrLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setPasteFlow(null);
    setPasteText('');
    (async () => {
      const s = await refreshStatus();
      await loadDefaultAddress();
      if (isConnected(s)) await loadAddresses();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const handleConnect = async () => {
    setError(null);
    if (isNative) {
      setBusy(true);
      try {
        await runNativeMcpConnect(PROVIDER);
        const s = await refreshStatus();
        if (isConnected(s)) await loadAddresses();
      } catch (err: any) {
        if (err?.message !== 'cancelled') setError(err?.message || 'Could not connect. Please try again.');
      } finally {
        setBusy(false);
      }
      return;
    }
    try {
      const data = await api.get(`/api/mcp/oauth/start?provider=${PROVIDER}`);
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
      console.error('Swiggy connect failed', err);
      setError(err?.message || 'Could not start the connection. Please try again.');
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
      if (isConnected(s)) await loadAddresses();
    } catch (err: any) {
      setError(err?.message || 'Could not finish connecting. Tap Connect to retry.');
    } finally {
      setPasteBusy(false);
    }
  };

  const handleSelectDefault = async (addressId: string) => {
    const row = addresses.find((a) => a.address_id === addressId);
    if (!row) return;
    setSavingAddr(true);
    setError(null);
    try {
      const data = await api.post('/api/mcp/instamart/default-address/', {
        address_id: row.address_id,
        tag: row.location_name,
        line: row.location_name,
      });
      setDefaultAddr(data?.default_address || { address_id: row.address_id, line: row.location_name });
      setPicking(false);
    } catch (err: any) {
      setError(err?.message || 'Could not save the address. Please try again.');
    } finally {
      setSavingAddr(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 md:p-4 pt-[max(0.5rem,var(--safe-top))] pb-[max(0.5rem,var(--safe-bottom))] bg-slate-950/40 backdrop-blur-md">
      <div className="w-full max-w-lg bg-white/95 border border-slate-200 rounded-2xl md:rounded-3xl shadow-2xl shadow-slate-900/20 flex flex-col max-h-[95vh] md:max-h-[90vh] overflow-hidden">
        <div className="flex items-center justify-between px-5 md:px-7 py-4 md:py-5 border-b border-slate-200">
          <h2 className="text-base md:text-lg font-bold flex items-center gap-2 text-slate-900">
            <ShoppingBag size={18} className="text-[#fc8019]" />
            Apps
          </h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-slate-100 rounded-full transition-colors text-slate-500 hover:text-slate-900"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 md:p-7 space-y-5">
          {/* Swiggy card */}
          <div className="p-5 bg-white border border-slate-200 rounded-2xl space-y-4">
            <div className="flex items-start gap-3">
              <div className="w-11 h-11 rounded-2xl bg-[#fc8019]/10 text-[#fc8019] flex items-center justify-center font-extrabold shrink-0">
                S
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="font-bold text-slate-900">{LABEL}</h3>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-[10px] font-black uppercase text-slate-500">Status:</span>
                  {connected ? (
                    <span className="text-[10px] font-black uppercase text-emerald-600 flex items-center gap-1">
                      <CheckCircle2 size={12} /> Connected
                    </span>
                  ) : (
                    <span className="text-[10px] font-black uppercase text-amber-600 flex items-center gap-1">
                      <AlertCircle size={12} /> {status?.status || 'Not connected'}
                    </span>
                  )}
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

            <button
              onClick={handleConnect}
              disabled={busy}
              className="w-full bg-slate-900 hover:bg-slate-800 disabled:opacity-60 text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm flex items-center justify-center gap-2"
            >
              {busy && <Loader2 size={14} className="animate-spin" />}
              {connected ? 'Reconnect Swiggy' : 'Connect Swiggy'}
            </button>
          </div>

          {/* Paste-back (web only, when Swiggy returns paste completion) */}
          {pasteFlow && !connected && (
            <div className="space-y-3 p-5 bg-blue-50/50 border border-blue-200 rounded-2xl">
              <label className="block text-[10px] font-black uppercase text-blue-700 tracking-wider">
                Finish Connecting
              </label>
              <a
                href={pasteFlow.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-center gap-2 w-full bg-blue-600 hover:bg-blue-700 text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-sm"
              >
                <ExternalLink size={14} /> Open Swiggy login
              </a>
              <p className="text-xs text-slate-700 leading-relaxed">
                After logging in you'll land on a page that can't open — that's expected. Copy that
                page's full address, come back here, and tap Paste &amp; Connect.
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
              {error && <p className="text-xs text-red-600">{error}</p>}
            </div>
          )}

          {/* Default delivery address (only once connected) */}
          {connected && (
            <div className="p-5 bg-white border border-slate-200 rounded-2xl space-y-3">
              <div className="flex items-center gap-2">
                <MapPin size={16} className="text-[#fc8019]" />
                <h3 className="font-bold text-sm text-slate-900">Default delivery address</h3>
              </div>
              <p className="text-xs text-slate-500 leading-relaxed">
                We'll use this address for all your Swiggy Instamart orders. You can change it anytime.
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
                    className="text-[11px] font-bold uppercase text-[#c2630f] hover:text-[#fc8019] tracking-wider shrink-0"
                  >
                    Change
                  </button>
                </div>
              ) : (
                <div className="space-y-2">
                  {addrLoading ? (
                    <div className="flex items-center gap-2 text-xs text-slate-500">
                      <Loader2 size={14} className="animate-spin" /> Loading your Swiggy addresses…
                    </div>
                  ) : addresses.length === 0 ? (
                    <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                      No saved Swiggy addresses found. Add one in the Swiggy app, then refresh.
                    </p>
                  ) : (
                    <select
                      defaultValue={defaultAddr?.address_id || ''}
                      disabled={savingAddr}
                      onChange={(e) => e.target.value && handleSelectDefault(e.target.value)}
                      className="w-full px-3 py-2.5 text-sm border border-slate-300 rounded-xl bg-white focus:outline-none focus:border-[#fc8019] disabled:opacity-60"
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

          {/* Swiggy Food card */}
          <SwiggyFoodApp isOpen={isOpen} />

          {/* Zepto card */}
          <ZeptoApp isOpen={isOpen} />

          {/* District card */}
          <DistrictApp isOpen={isOpen} />
        </div>
      </div>
    </div>
  );
};
