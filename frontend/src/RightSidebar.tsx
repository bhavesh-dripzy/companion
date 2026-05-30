import React, { useMemo } from 'react';
import {
  Layers,
  Settings as SettingsIcon,
  Calendar,
  MapPin,
  Check,
  Loader2,
  BrainCircuit,
  Share2,
  LogIn,
  X,
} from 'lucide-react';
import type { McpStatus, ToolProvider } from './types';

interface RightSidebarProps {
  username: string;
  toolProviders: ToolProvider[];
  mcpStatuses: Record<string, McpStatus>;
  isLoadingMcp: boolean;
  onSelectProvider: (id: string) => void;
  gcalStatus: { connected: boolean; configured: boolean } | null;
  onConnectGcal: () => void;
  mapsConfigured: boolean | null;
  onOpenMaps: () => void;
  onOpenSettings: () => void;
  isMobileOpen: boolean;
  onMobileClose: () => void;
}

// Provider-id → branded badge. Falls back to slate for anything we
// don't have a custom colour for (Zapier, future MCPs, etc.). The
// letter is the upper-case first character of the provider id so a
// brand-new MCP server slots in without code changes.
const PROVIDER_THEME: Record<string, string> = {
  zomato: 'bg-rose-500',
  swiggy: 'bg-orange-500',
  uber: 'bg-slate-900',
  zerodha: 'bg-blue-600',
  whatsapp: 'bg-emerald-500',
  gmail: 'bg-red-500',
  district: 'bg-indigo-600',
  zepto: 'bg-violet-600',
  blinkit: 'bg-amber-500',
  zapier: 'bg-orange-600',
};

const providerColor = (id: string): string => PROVIDER_THEME[id?.toLowerCase()] || 'bg-slate-700';
const providerLetter = (label: string, id: string): string =>
  (label || id || '?').trim().charAt(0).toUpperCase();

function isProviderConnected(status?: McpStatus | null): boolean {
  if (!status) return false;
  const s = status.status;
  if (s === 'ready' || s === 'ready_pending') return true;
  if ((status as any).has_local_tokens || (status as any).has_secret_tokens) return true;
  return false;
}

const GoogleIconSVG: React.FC = () => (
  <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="currentColor">
    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4" />
    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
  </svg>
);

// The right-hand info panel. Every row is wired to a real backend
// signal — there are no mock states left. Clicking an MCP card or
// either Google integration jumps to the Settings modal (scoped to
// that provider when relevant) which is where pairing / disconnect /
// route-preview live so the rail stays calm and read-mostly.
export const RightSidebar: React.FC<RightSidebarProps> = ({
  username,
  toolProviders,
  mcpStatuses,
  isLoadingMcp,
  onSelectProvider,
  gcalStatus,
  onConnectGcal,
  mapsConfigured,
  onOpenMaps,
  onOpenSettings,
  isMobileOpen,
  onMobileClose,
}) => {
  const availableProviders = useMemo(
    () => toolProviders.filter((p) => !!p && p.available !== false),
    [toolProviders],
  );

  const numMcpConnected = availableProviders.filter((p) => isProviderConnected(mcpStatuses[p.id])).length;

  // Wrap every action so opening Settings / connecting Google etc. also
  // dismisses the mobile drawer — otherwise the modal that pops up
  // would be hidden behind the still-open right panel.
  const tap = (fn: () => void) => () => {
    fn();
    onMobileClose();
  };

  return (
    <>
      <div
        onClick={onMobileClose}
        className={`md:hidden fixed inset-0 bg-slate-950/40 backdrop-blur-sm z-30 transition-opacity ${
          isMobileOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
        }`}
      />

      <div
        id="right-sidebar"
        className={`bg-white/95 md:bg-white/40 backdrop-blur-xl border-l border-slate-200/60 flex flex-col p-5 shrink-0 select-none overflow-y-auto justify-between transition-transform duration-300 ease-out fixed md:static inset-y-0 right-0 z-40 w-80 md:w-72 ${
          isMobileOpen ? 'translate-x-0' : 'translate-x-full md:translate-x-0'
        }`}
      >
        <button
          onClick={onMobileClose}
          className="md:hidden absolute -left-3 top-3 w-7 h-7 bg-white border border-slate-200 rounded-full flex items-center justify-center shadow-sm text-slate-600 hover:text-slate-900"
          title="Close panel"
          aria-label="Close panel"
        >
          <X size={14} />
        </button>

        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <button
              onClick={tap(onOpenSettings)}
              className="flex items-center gap-2 bg-slate-900/5 hover:bg-slate-900/10 active:scale-98 transition-all px-3.5 py-1.5 rounded-full border border-slate-200/70 text-slate-800 text-xs font-semibold cursor-pointer"
              title="Open Settings"
            >
              <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              <BrainCircuit size={13} className="text-slate-600" />
              <span className="truncate max-w-[140px]">Companion · {username || 'Guest'}</span>
            </button>

            <button
              onClick={tap(onOpenSettings)}
              className="p-1.5 text-slate-500 hover:text-slate-800 bg-slate-100/40 hover:bg-slate-100/80 rounded-lg transition-colors cursor-pointer"
              title="Share / configure workspace"
            >
              <Share2 size={13} />
            </button>
          </div>

        {/* ---------------- MCP SERVERS ---------------- */}
        <div className="space-y-3">
          <div className="flex items-center justify-between pb-1 border-b border-slate-200/70">
            <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
              <Layers size={12} className="text-slate-500" />
              <span>MCP Servers</span>
            </h3>
            <span className="text-[10px] bg-slate-900/5 text-slate-700 py-0.5 px-2 rounded-full font-bold">
              {numMcpConnected} / {availableProviders.length || 0}
            </span>
          </div>

          <div className="grid grid-cols-1 gap-2">
            {availableProviders.length === 0 && (
              <div className="text-[11px] text-slate-500 italic px-1 py-2">
                {isLoadingMcp ? 'Loading providers…' : 'No MCP servers available.'}
              </div>
            )}
            {availableProviders.map((p) => {
              const status = mcpStatuses[p.id];
              const connected = isProviderConnected(status);
              return (
                <button
                  key={p.id}
                  onClick={tap(() => onSelectProvider(p.id))}
                  className={`w-full flex items-center justify-between p-2.5 rounded-xl border transition-all cursor-pointer select-none text-left ${
                    connected
                      ? 'bg-white/90 border-slate-200 shadow-sm hover:translate-x-0.5'
                      : 'bg-slate-50/60 border-slate-200/70 hover:bg-slate-50 text-slate-500 hover:translate-x-0.5'
                  }`}
                  title={`${connected ? 'Manage' : 'Connect'} ${p.label}`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div
                      className={`w-7 h-7 rounded-lg flex items-center justify-center font-bold text-xs text-white shadow-sm shrink-0 ${providerColor(p.id)}`}
                    >
                      {providerLetter(p.label, p.id)}
                    </div>
                    <div className="flex flex-col min-w-0">
                      <span className="text-xs font-semibold text-slate-800 truncate">{p.label}</span>
                      {typeof p.tool_count === 'number' && p.tool_count > 0 && (
                        <span className="text-[9px] text-slate-400 font-medium">
                          {p.tool_count} tools
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 shrink-0">
                    <div
                      className={`w-1.5 h-1.5 rounded-full ${
                        connected ? 'bg-emerald-500 animate-pulse' : 'bg-slate-300'
                      }`}
                    />
                    <span
                      className={`text-[10px] font-bold ${
                        connected ? 'text-emerald-600' : 'text-slate-400'
                      }`}
                    >
                      {connected ? 'Connected' : 'Connect'}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* ---------------- INTEGRATIONS ---------------- */}
        <div className="space-y-4">
          <div className="pb-1 border-b border-slate-200/70">
            <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
              Integrations
            </h3>
          </div>

          {/* Google Calendar */}
          <div className="bg-white/85 border border-slate-200 p-3 rounded-2xl space-y-2.5 shadow-sm">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-blue-50 text-blue-600">
                  <Calendar size={14} />
                </div>
                <span className="text-xs font-bold text-slate-800">Google Calendar</span>
              </div>
              {gcalStatus?.connected && (
                <span className="text-[9px] bg-emerald-50 text-emerald-700 py-0.5 px-2 rounded-full font-bold flex items-center gap-0.5">
                  <Check size={8} /> Active
                </span>
              )}
            </div>

            {gcalStatus === null ? (
              <button
                disabled
                className="w-full bg-slate-100 text-slate-400 text-[11px] py-1.5 px-3 rounded-xl flex items-center justify-center gap-2 font-semibold"
              >
                <Loader2 size={12} className="animate-spin" />
                <span>Checking…</span>
              </button>
            ) : gcalStatus.connected ? (
              <button
                onClick={tap(onConnectGcal)}
                className="w-full bg-white text-slate-800 border border-slate-200 hover:border-slate-300 hover:bg-slate-50 text-[11px] py-1.5 px-3 rounded-xl flex items-center justify-center gap-2 font-semibold cursor-pointer shadow-sm"
              >
                <GoogleIconSVG />
                <span>Reconnect Google</span>
              </button>
            ) : (
              <button
                onClick={tap(onConnectGcal)}
                className="w-full bg-slate-900 hover:bg-slate-800 text-white hover:scale-[1.01] active:scale-99 transition-all text-[11px] py-1.5 px-3 rounded-xl flex items-center justify-center gap-2 font-semibold cursor-pointer shadow-sm"
              >
                <div className="bg-white p-0.5 rounded-sm flex items-center justify-center">
                  <GoogleIconSVG />
                </div>
                <span>Sign in with Google</span>
              </button>
            )}
          </div>

          {/* Google Maps */}
          <div className="bg-white/85 border border-slate-200 p-3 rounded-2xl space-y-2.5 shadow-sm">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-emerald-50 text-emerald-600">
                  <MapPin size={14} />
                </div>
                <span className="text-xs font-bold text-slate-800">Google Maps</span>
              </div>
              {mapsConfigured && (
                <span className="text-[9px] bg-emerald-50 text-emerald-700 py-0.5 px-2 rounded-full font-bold flex items-center gap-0.5">
                  <Check size={8} /> Ready
                </span>
              )}
            </div>

            {mapsConfigured === null ? (
              <button
                disabled
                className="w-full bg-slate-100 text-slate-400 text-[11px] py-1.5 px-3 rounded-xl flex items-center justify-center gap-2 font-semibold"
              >
                <Loader2 size={12} className="animate-spin" />
                <span>Checking…</span>
              </button>
            ) : (
              <button
                onClick={tap(onOpenMaps)}
                className={`w-full text-[11px] py-1.5 px-3 rounded-xl flex items-center justify-center gap-2 font-semibold cursor-pointer transition-all shadow-sm ${
                  mapsConfigured
                    ? 'bg-slate-900 hover:bg-slate-800 text-white'
                    : 'bg-white text-slate-800 border border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                }`}
              >
                <MapPin size={12} />
                <span>{mapsConfigured ? 'Compute distance & ETA' : 'Set up Maps'}</span>
              </button>
            )}
          </div>
        </div>

          <div className="pt-2 border-t border-slate-200/70">
            <button
              onClick={tap(onOpenSettings)}
              className="w-full bg-slate-50/70 hover:bg-white p-2.5 rounded-xl border border-slate-200/70 hover:border-slate-300 text-[11px] text-slate-600 hover:text-slate-900 font-medium transition-all flex items-center justify-center gap-2 cursor-pointer"
            >
              <LogIn size={11} />
              Manage connections in Settings
            </button>
          </div>
      </div>

        <div className="mt-8 pt-4 border-t border-slate-200/60 flex items-center justify-between text-[11px] text-slate-400">
          <div className="flex items-center gap-1">
            <SettingsIcon size={11} />
            <span>Companion · Live Sync</span>
          </div>
          <span className="font-mono">Secure</span>
        </div>
      </div>
    </>
  );
};
