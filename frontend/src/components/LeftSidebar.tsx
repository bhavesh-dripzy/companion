/**
 * Companion left sidebar (new visual shell + real backend data).
 *
 * Wired to:
 *   - chats:        /api/chats/ list (with delete + select handlers)
 *   - tool tiles:   /api/tools/providers/ + /api/mcp/status/all/
 *   - settings:     opens the existing SettingsModal on tile click
 *   - admin:        gated "Admin" button visible only when isAdmin
 *   - reminders /
 *     orders:       local-only popups (no backend endpoints)
 *
 * Mirrors the redesign visually (Companion v2.6 header → "+ New Chat" →
 * navigation pills → grouped conversation list → Connected Apps grid →
 * footer with Admin + Logout) but keeps the existing app's behaviour
 * 1:1 — no MCP toggle stubs, no fake chat threads.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Plus,
  ChevronRight,
  Search,
  MessageSquare,
  Bell,
  Heart,
  ShoppingBag,
  Users,
  Settings,
  LogOut,
  Shield,
  Trash2,
} from 'lucide-react';
import type { ChatSummary, McpStatus, ToolProvider } from '../types';

interface LeftSidebarProps {
  // Auth + nav
  username: string;
  isAdmin: boolean;
  onLogout: () => void;
  onOpenAdmin?: () => void;
  onOpenSettings: () => void;
  onNewChat: () => void;

  // Chats
  chats: ChatSummary[];
  activeChatId: string | null;
  onSelectChat: (id: string) => void;
  onDeleteChat: (id: string) => void;

  // Real MCP / providers
  toolProviders: ToolProvider[];
  mcpStatuses: Record<string, McpStatus>;
  onSelectProvider: (id: string) => void;

  // Reminders / Orders popups (local-only)
  remindersBadge?: number;
  onShowReminders: () => void;
  onShowOrders: () => void;

  // Layout
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

// Provider id → branded background colour for the small square tile.
// Falls back to slate-700 for anything we don't know; the letter is
// drawn from the provider id so a brand-new MCP slots in automatically.
const PROVIDER_THEME: Record<string, { bg: string; fg: string }> = {
  zomato: { bg: 'bg-red-600', fg: 'text-white' },
  zomato_food: { bg: 'bg-red-600', fg: 'text-white' },
  swiggy: { bg: 'bg-orange-500', fg: 'text-white' },
  swiggy_food: { bg: 'bg-orange-500', fg: 'text-white' },
  instamart: { bg: 'bg-orange-400', fg: 'text-white' },
  instamart_food: { bg: 'bg-orange-400', fg: 'text-white' },
  zepto: { bg: 'bg-violet-600', fg: 'text-white' },
  blinkit: { bg: 'bg-amber-400', fg: 'text-slate-900' },
  uber: { bg: 'bg-slate-900', fg: 'text-white' },
  district: { bg: 'bg-indigo-600', fg: 'text-white' },
  zerodha: { bg: 'bg-blue-600', fg: 'text-white' },
  groww: { bg: 'bg-emerald-500', fg: 'text-white' },
  whatsapp: { bg: 'bg-emerald-500', fg: 'text-white' },
  gmail: { bg: 'bg-rose-500', fg: 'text-white' },
  zapier: { bg: 'bg-orange-600', fg: 'text-white' },
};

const NEUTRAL_THEME = { bg: 'bg-slate-700', fg: 'text-white' };

const themeForProvider = (id: string) => PROVIDER_THEME[id?.toLowerCase()] || NEUTRAL_THEME;

const providerLetter = (label: string, id: string): string =>
  (label || id || '?').trim().charAt(0).toUpperCase();

const isProviderConnected = (status?: McpStatus | null): boolean => {
  if (!status) return false;
  const s = status.status;
  if (s === 'ready' || s === 'ready_pending') return true;
  if ((status as any).has_local_tokens || (status as any).has_secret_tokens) return true;
  return false;
};

// Bucket a chat into a friendly date group based on its last activity
// timestamp. Anything we can't parse drops into "Earlier".
const groupForChat = (chat: ChatSummary): 'Today' | 'Yesterday' | 'Past Week' | 'Earlier' => {
  const stamp = chat.last_message_at || chat.updated_at || chat.created_at;
  if (!stamp) return 'Earlier';
  const t = Date.parse(stamp);
  if (Number.isNaN(t)) return 'Earlier';

  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startOfYesterday = startOfToday - 24 * 60 * 60 * 1000;
  const startOfWeekAgo = startOfToday - 7 * 24 * 60 * 60 * 1000;

  if (t >= startOfToday) return 'Today';
  if (t >= startOfYesterday) return 'Yesterday';
  if (t >= startOfWeekAgo) return 'Past Week';
  return 'Earlier';
};

const formatChatTimeLabel = (chat: ChatSummary): string => {
  const stamp = chat.last_message_at || chat.updated_at || chat.created_at;
  if (!stamp) return '';
  const d = new Date(stamp);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
  if (sameDay) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
};

export function LeftSidebar({
  username,
  isAdmin,
  onLogout,
  onOpenAdmin,
  onOpenSettings,
  onNewChat,
  chats,
  activeChatId,
  onSelectChat,
  onDeleteChat,
  toolProviders,
  mcpStatuses,
  onSelectProvider,
  remindersBadge,
  onShowReminders,
  onShowOrders,
  isCollapsed,
  onToggleCollapse,
}: LeftSidebarProps) {
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  // Auto-clear the delete confirm overlay after a few seconds so a
  // stray hover doesn't leave it locked in red mode.
  useEffect(() => {
    if (!pendingDelete) return;
    const t = setTimeout(() => setPendingDelete(null), 4000);
    return () => clearTimeout(t);
  }, [pendingDelete]);

  const availableProviders = useMemo(
    () => toolProviders.filter((p) => p && p.available !== false),
    [toolProviders],
  );

  const connectedCount = availableProviders.filter((p) =>
    isProviderConnected(mcpStatuses[p.id]),
  ).length;
  const totalCount = availableProviders.length;

  // Group chats into the four buckets the redesign expects. Order
  // within each bucket follows the order the backend returned (most
  // recent first — `/api/chats/` already sorts that way).
  const groupedChats = useMemo(() => {
    const buckets: Record<string, ChatSummary[]> = {
      Today: [],
      Yesterday: [],
      'Past Week': [],
      Earlier: [],
    };
    for (const chat of chats) {
      buckets[groupForChat(chat)].push(chat);
    }
    return buckets;
  }, [chats]);

  if (isCollapsed) return null;

  type NavPage = {
    id: string;
    label: string;
    icon: typeof Bell;
    bg: string;
    badge?: number;
    onClick?: () => void;
  };
  const navigationPages: NavPage[] = [
    {
      id: 'reminders',
      label: 'Reminders',
      icon: Bell,
      badge: remindersBadge,
      bg: 'bg-purple-50 text-purple-600 border-purple-100',
      onClick: onShowReminders,
    },
    {
      id: 'health',
      label: 'Health',
      icon: Heart,
      bg: 'bg-rose-50 text-rose-500 border-rose-100',
    },
    {
      id: 'orders',
      label: 'Orders',
      icon: ShoppingBag,
      bg: 'bg-amber-50 text-amber-600 border-amber-100',
      onClick: onShowOrders,
    },
    {
      id: 'family',
      label: 'Family',
      icon: Users,
      bg: 'bg-indigo-50 text-indigo-600 border-indigo-100',
    },
  ];

  const handleDeleteClick = (chatId: string) => {
    if (pendingDelete === chatId) {
      setPendingDelete(null);
      onDeleteChat(chatId);
    } else {
      setPendingDelete(chatId);
    }
  };

  const renderChatRow = (chat: ChatSummary) => {
    const isActive = activeChatId === chat.chat_id;
    const isPending = pendingDelete === chat.chat_id;
    return (
      <div
        key={chat.chat_id}
        onClick={() => {
          if (isPending) {
            setPendingDelete(null);
            return;
          }
          onSelectChat(chat.chat_id);
        }}
        className={`group relative flex items-center justify-between py-1.5 px-2 rounded-[11px] border transition-all duration-300 cursor-pointer ${
          isActive
            ? 'bg-[#ff8a1e]/10 border-[#ff8a1e]/25 shadow-[0_2px_6px_-2px_rgba(255,138,30,0.3)] font-semibold text-[#221b2e]'
            : 'bg-transparent border-transparent hover:bg-white/50 text-[#544e63] hover:text-[#221b2e]'
        }`}
        title={chat.title || 'Untitled chat'}
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <MessageSquare
            className={`w-3.5 h-3.5 shrink-0 transition-colors ${
              isActive ? 'text-[#ff8a1e]' : 'text-slate-400/70'
            }`}
          />
          <span className="text-[12.5px] font-bold truncate">
            {chat.title || 'New conversation'}
          </span>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-[10px] font-mono text-slate-400 select-none pl-1.5 group-hover:text-slate-500 hidden group-hover:inline">
            {formatChatTimeLabel(chat)}
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              handleDeleteClick(chat.chat_id);
            }}
            className={`p-1 rounded transition-all ${
              isPending
                ? 'text-rose-600 bg-rose-50'
                : 'text-slate-300 hover:text-rose-500 hover:bg-rose-50 opacity-0 group-hover:opacity-100'
            }`}
            title={isPending ? 'Click again to delete' : 'Delete chat'}
            aria-label="Delete chat"
          >
            <Trash2 size={11} />
          </button>
          {!isPending && (
            <span className="text-[10px] font-mono text-slate-400 select-none pl-1.5 group-hover:hidden">
              {formatChatTimeLabel(chat)}
            </span>
          )}
        </div>
      </div>
    );
  };

  const chatGroups: Array<{ key: string; label: string; rows: ChatSummary[] }> = [
    { key: 'Today', label: 'Today', rows: groupedChats.Today },
    { key: 'Yesterday', label: 'Yesterday', rows: groupedChats.Yesterday },
    { key: 'Past Week', label: 'Past Week', rows: groupedChats['Past Week'] },
    { key: 'Earlier', label: 'Earlier', rows: groupedChats.Earlier },
  ];

  return (
    <>
      {/* Mobile-only scrim: on phones the sidebar floats over the chat as a
          drawer, so a tap outside dismisses it. Hidden on md+ where the
          sidebar is an inline column. */}
      <div
        className="fixed inset-0 z-40 bg-[#221b2e]/40 backdrop-blur-[1px] md:hidden"
        onClick={onToggleCollapse}
        aria-hidden
      />
      <div className="fixed md:relative inset-y-0 left-0 z-50 md:z-auto w-80 max-w-[86vw] md:max-w-none bg-white/[0.97] md:bg-white/[0.66] backdrop-blur-xl border-r border-[#ece3d6]/80 flex flex-col h-full text-[#221b2e] select-none shrink-0 shadow-2xl md:shadow-none transition-all duration-300">
      {/* Header */}
      <div className="py-2.5 px-4 flex items-center justify-between shrink-0 border-b border-[#ece3d6]/70">
        <div className="flex items-center gap-2.5">
          <div className="brand-orb w-8.5 h-8.5 shrink-0 animate-orb-glow" />
          <div>
            <div className="flex items-center gap-1.5">
              <span className="text-[#221b2e] font-semibold text-[19px] font-display tracking-tight leading-none">
                Companion
              </span>
              <span className="bg-[#2b2f72]/8 text-[#2b2f72] px-1.5 py-0.5 rounded text-[8.5px] font-extrabold font-mono tracking-tight leading-none border border-[#2b2f72]/15">
                v2.6
              </span>
            </div>
            <span className="text-[10.5px] text-[#7a7388] block font-medium mt-0.5 font-sans leading-none">
              Bharat&rsquo;s everything-assistant
            </span>
          </div>
        </div>

        <button
          onClick={onToggleCollapse}
          className="p-1.5 hover:bg-white border border-[#ece3d6]/40 text-[#7a7388] hover:text-[#221b2e] rounded-lg transition-all cursor-pointer flex items-center justify-center shadow-3xs"
          title="Collapse sidebar"
        >
          <svg
            className="w-3.5 h-3.5 text-slate-500"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            viewBox="0 0 24 24"
          >
            <rect x="3" y="3" width="18" height="18" rx="3.5" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 3v18" />
          </svg>
        </button>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto px-3.5 pb-3.5 pt-3 space-y-2.5 no-scrollbar">
        {/* + New Chat */}
        <div className="px-0.5">
          <button
            onClick={onNewChat}
            className="btn-spectrum w-full font-bold text-[13.5px] py-2.5 px-3.5 rounded-xl flex items-center justify-between active:scale-[0.98] cursor-pointer group font-display"
          >
            <div className="flex items-center gap-1.5">
              <Plus className="w-3.5 h-3.5 text-white stroke-[2.5]" />
              <span>New Chat</span>
            </div>
            <span className="text-[10px] text-slate-300 px-1.5 py-0.5 bg-white/10 rounded font-mono font-semibold tracking-tight uppercase">
              new
            </span>
          </button>
        </div>

        {/* Navigation pills */}
        <nav className="space-y-0.5">
          {navigationPages.map((page) => {
            const IconComponent = page.icon;
            const isTabActive = activeTab === page.id;
            return (
              <button
                key={page.id}
                onClick={() => {
                  setActiveTab(page.id);
                  page.onClick?.();
                }}
                className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg transition-all duration-150 font-medium text-[13.5px] font-sans text-left cursor-pointer group ${
                  isTabActive
                    ? 'bg-white text-slate-900 font-bold border border-slate-200/60 shadow-[0_1.5px_3px_rgba(0,0,0,0.01)]'
                    : 'text-slate-600 hover:bg-white/60 hover:text-slate-900'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <div
                    className={`p-1 rounded-md border transition-all ${
                      isTabActive ? page.bg : 'bg-white border-slate-100 group-hover:bg-slate-50 text-slate-500'
                    }`}
                  >
                    <IconComponent className="w-3.5 h-3.5 stroke-[2.2]" />
                  </div>
                  <span
                    className={
                      isTabActive ? 'font-bold text-slate-900' : 'font-medium text-slate-600'
                    }
                  >
                    {page.label}
                  </span>
                </div>
                {page.badge ? (
                  <span className="text-[10px] font-extrabold text-white bg-indigo-500 px-1.5 rounded-full font-mono">
                    {page.badge}
                  </span>
                ) : (
                  <ChevronRight
                    className={`w-3 h-3 transition-all text-slate-300 group-hover:text-slate-500 ${
                      isTabActive ? 'opacity-100 translate-x-0.5' : 'opacity-0 -translate-x-1'
                    }`}
                  />
                )}
              </button>
            );
          })}
        </nav>

        {/* Recent Conversations */}
        <div className="space-y-2 pt-1">
          <div className="flex items-center justify-between px-1 pb-0.5">
            <span className="text-[11px] font-extrabold text-slate-400 font-display uppercase tracking-wider">
              Recent Conversations
            </span>
            <Search
              className="w-3.5 h-3.5 text-slate-400 cursor-pointer hover:text-slate-800 transition-colors"
              aria-hidden
            />
          </div>

          {chats.length === 0 ? (
            <div className="text-center py-4 px-3 bg-white/40 border border-dashed border-slate-200 rounded-xl text-[11px] font-medium text-slate-400">
              No conversations yet. Start one with the input below.
            </div>
          ) : (
            chatGroups
              .filter((g) => g.rows.length > 0)
              .map((g) => (
                <div key={g.key} className="space-y-0.5">
                  <div className="text-[9.5px] font-bold text-slate-400/70 uppercase tracking-widest px-2 pb-0.5 select-none">
                    {g.label}
                  </div>
                  {g.rows.map(renderChatRow)}
                </div>
              ))
          )}
        </div>

        {/* Connected Apps grid (real MCP statuses) */}
        <div className="space-y-2 pt-3.5 border-t border-slate-200/50">
          <div className="flex items-center justify-between px-1 pb-0.5">
            <span className="text-[11px] font-extrabold text-slate-400 font-display uppercase tracking-wider">
              Connected Apps
            </span>
            <span className="text-[9.5px] font-mono font-bold text-slate-500 bg-slate-200/50 px-1.5 py-0.5 rounded-full select-none">
              {connectedCount}/{totalCount} Active
            </span>
          </div>

          {availableProviders.length === 0 ? (
            <div className="text-center py-3 px-3 bg-white/40 border border-dashed border-slate-200 rounded-xl text-[11px] font-medium text-slate-400">
              No providers configured yet.
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {availableProviders.map((server) => {
                const status = mcpStatuses[server.id];
                const isConnected = isProviderConnected(status);
                const theme = themeForProvider(server.id);
                const cleanedName = (server.label || server.id || 'App').replace(/\s+MCP$/i, '');
                return (
                  <div
                    key={server.id}
                    onClick={() => onSelectProvider(server.id)}
                    className="group relative flex items-center gap-2 p-2 rounded-lg bg-white/60 hover:bg-white border border-slate-200/40 hover:border-slate-300/80 transition-all duration-200 cursor-pointer shadow-3xs active:scale-[0.98]"
                    title={`Click to manage ${cleanedName}`}
                  >
                    <div
                      className={`w-6.5 h-6.5 rounded-md flex items-center justify-center font-bold text-[11px] shrink-0 ${
                        isConnected ? `${theme.bg} ${theme.fg}` : 'bg-slate-200 text-slate-400'
                      }`}
                    >
                      {providerLetter(server.label, server.id)}
                    </div>
                    <div className="min-w-0 flex-1 flex flex-col justify-center">
                      <span className="text-[11.5px] font-bold text-slate-800 truncate group-hover:text-indigo-650 transition-colors">
                        {cleanedName}
                      </span>
                      <div className="flex items-center gap-1">
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${
                            isConnected ? 'bg-emerald-500 animate-pulse' : 'bg-slate-300'
                          }`}
                        />
                        <span className="text-[8.5px] font-medium text-slate-405 select-none">
                          {isConnected ? 'Connected' : 'Offline'}
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="shrink-0 border-t border-[#ece3d6]/70 bg-white/40 backdrop-blur-sm px-3 py-2.5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <div className="w-7.5 h-7.5 rounded-[9px] bg-[#221b2e] text-white flex items-center justify-center font-bold text-[12px] shadow-sm shrink-0">
            {(username || 'C').charAt(0).toUpperCase()}
          </div>
          <span className="text-[12px] font-bold text-[#221b2e] truncate capitalize">
            {username || 'Companion'}
          </span>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {isAdmin && onOpenAdmin && (
            <button
              onClick={onOpenAdmin}
              className="p-1.5 rounded-lg text-slate-500 hover:text-amber-600 hover:bg-amber-50 transition-colors cursor-pointer"
              title="Admin dashboard"
              aria-label="Admin dashboard"
            >
              <Shield size={14} />
            </button>
          )}
          <button
            onClick={onOpenSettings}
            className="p-1.5 rounded-lg text-slate-500 hover:text-slate-900 hover:bg-slate-100 transition-colors cursor-pointer"
            title="Settings"
            aria-label="Open settings"
          >
            <Settings size={14} />
          </button>
          <button
            onClick={onLogout}
            className="p-1.5 rounded-lg text-slate-500 hover:text-rose-600 hover:bg-rose-50 transition-colors cursor-pointer"
            title="Log out"
            aria-label="Log out"
          >
            <LogOut size={14} />
          </button>
        </div>
      </div>
    </div>
    </>
  );
}
