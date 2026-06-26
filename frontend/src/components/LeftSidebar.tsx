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
  LayoutGrid,
  CheckSquare,
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

  // Apps onboarding (Swiggy)
  onOpenApps?: () => void;
  /** Whether the Apps modal is open — drives the nav highlight so it clears when the modal closes. */
  appsOpen?: boolean;

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
  onOpenApps,
  appsOpen,
  isCollapsed,
  onToggleCollapse,
}: LeftSidebarProps) {
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
    // Visible placeholder for a not-yet-built feature: rendered, but a click is a no-op
    // (no action, no active highlight) until we wire it up.
    inert?: boolean;
  };
  const navigationPages: NavPage[] = [
    {
      id: 'reminders',
      label: 'Reminders',
      icon: Bell,
      bg: 'bg-purple-50 text-purple-600 border-purple-100',
      inert: true,
    },
    {
      id: 'health',
      label: 'Health',
      icon: Heart,
      bg: 'bg-rose-50 text-rose-500 border-rose-100',
      inert: true,
    },
    {
      id: 'apps',
      label: 'Apps',
      icon: LayoutGrid,
      bg: 'bg-blue-50 text-blue-600 border-blue-100',
      onClick: onOpenApps,
    },
    {
      id: 'daily-tasks',
      label: 'Daily Tasks',
      icon: CheckSquare,
      bg: 'bg-emerald-50 text-emerald-600 border-emerald-100',
      inert: true,
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
        className={`group relative flex items-center justify-between py-1.5 px-3 rounded-lg transition-all duration-150 cursor-pointer ${
          isActive
            ? 'bg-slate-900/8 text-slate-900'
            : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
        }`}
        title={chat.title || 'Untitled chat'}
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <MessageSquare
            className={`w-3.5 h-3.5 shrink-0 transition-colors ${
              isActive ? 'text-slate-900' : 'text-slate-400'
            }`}
          />
          <span className={`text-[13px] truncate ${isActive ? 'font-medium text-slate-900' : 'font-normal text-slate-600'}`}>
            {chat.title || 'New conversation'}
          </span>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          <span className="text-[10px] text-slate-400 select-none group-hover:hidden">
            {formatChatTimeLabel(chat)}
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              handleDeleteClick(chat.chat_id);
            }}
            className={`p-1 rounded transition-all hidden group-hover:flex items-center justify-center ${
              isPending
                ? 'text-rose-600 bg-rose-50'
                : 'text-slate-400 hover:text-rose-500 hover:bg-rose-50'
            }`}
            title={isPending ? 'Click again to delete' : 'Delete chat'}
            aria-label="Delete chat"
          >
            <Trash2 size={11} />
          </button>
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
      <div className="fixed md:relative left-0 z-50 md:z-auto w-72 max-w-[86vw] md:max-w-none bg-[#F6F8FC] border-r border-[#E9EEF5] flex flex-col text-slate-900 select-none shrink-0 shadow-xl md:shadow-none transition-all duration-300 top-0 bottom-0 h-auto md:top-auto md:bottom-auto md:inset-y-0 md:h-full">
      {/* Header — top padding accounts for the device status bar */}
      <div className="pt-[calc(var(--safe-top)+0.875rem)] pb-3.5 px-4 flex items-center justify-between shrink-0 border-b border-[#E9EEF5]">
        <div className="flex items-center gap-2.5">
          {/* Logo mark */}
          <img
            src="/omi-logo.png"
            alt="omi"
            draggable={false}
            className="w-8 h-8 rounded-xl shrink-0 object-cover select-none"
          />
          <span className="text-slate-900 font-semibold text-[15px] font-sans tracking-tight leading-none">
            omi
          </span>
        </div>

        <button
          onClick={onToggleCollapse}
          className="p-1.5 hover:bg-slate-100 text-slate-400 hover:text-slate-700 rounded-lg transition-all cursor-pointer flex items-center justify-center"
          title="Collapse sidebar"
        >
          <svg
            className="w-4 h-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            viewBox="0 0 24 24"
          >
            <rect x="3" y="3" width="18" height="18" rx="3.5" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 3v18" />
          </svg>
        </button>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto px-3 pb-3 pt-2.5 space-y-1 no-scrollbar">
        {/* + New Chat */}
        <div className="pb-1">
          <button
            onClick={onNewChat}
            className="btn-spectrum w-full font-medium text-[13px] py-2 px-3.5 rounded-xl flex items-center justify-between active:scale-[0.98] cursor-pointer group font-sans"
          >
            <div className="flex items-center gap-2">
              <Plus className="w-4 h-4 text-white stroke-[2]" />
              <span>New Chat</span>
            </div>
          </button>
        </div>

        {/* Navigation pills */}
        <nav className="space-y-0.5 py-1">
          {navigationPages.map((page) => {
            const IconComponent = page.icon;
            const isTabActive = page.id === 'apps' && !!appsOpen;
            return (
              <button
                key={page.id}
                onClick={() => {
                  if (page.inert) return; // placeholder option — no-op until the feature ships
                  page.onClick?.();
                }}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-lg transition-all duration-150 text-[13px] font-sans text-left cursor-pointer group ${
                  isTabActive
                    ? 'bg-slate-900 text-white'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <IconComponent className={`w-4 h-4 stroke-[1.75] shrink-0 ${isTabActive ? 'text-white' : 'text-slate-500'}`} />
                  <span className={`font-medium ${isTabActive ? 'text-white' : 'text-slate-700'}`}>
                    {page.label}
                  </span>
                </div>
                {page.badge ? (
                  <span className="text-[10px] font-semibold text-white bg-indigo-500 px-1.5 py-0.5 rounded-full font-sans">
                    {page.badge}
                  </span>
                ) : (
                  <ChevronRight
                    className={`w-3.5 h-3.5 transition-all ${
                      isTabActive ? 'text-white/60 opacity-100' : 'text-slate-300 opacity-0 group-hover:opacity-100'
                    }`}
                  />
                )}
              </button>
            );
          })}
        </nav>

        {/* Recent Conversations */}
        <div className="space-y-1 pt-2 border-t border-slate-200">
          <div className="flex items-center justify-between px-3 pb-1">
            <span className="text-[11px] font-medium text-slate-400 font-sans">
              Recent Conversations
            </span>
            <Search
              className="w-3.5 h-3.5 text-slate-400 cursor-pointer hover:text-slate-700 transition-colors"
              aria-hidden
            />
          </div>

          {chats.length === 0 ? (
            <div className="mx-1 py-4 px-3 bg-slate-50 border border-slate-200 rounded-lg text-[12px] font-normal text-slate-400 text-center">
              No conversations yet.
            </div>
          ) : (
            chatGroups
              .filter((g) => g.rows.length > 0)
              .map((g) => (
                <div key={g.key} className="space-y-0.5">
                  <div className="text-[10px] font-medium text-slate-400 px-3 pt-1 pb-0.5 select-none">
                    {g.label}
                  </div>
                  {g.rows.map(renderChatRow)}
                </div>
              ))
          )}
        </div>

        {/* Connected Apps grid (real MCP statuses) */}
        <div className="space-y-1.5 pt-2 border-t border-slate-200">
          <div className="flex items-center justify-between px-3 pb-1">
            <span className="text-[11px] font-medium text-slate-400 font-sans">
              Connected Apps
            </span>
            <span className="text-[10px] font-medium text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded-full select-none font-sans">
              {connectedCount}/{totalCount}
            </span>
          </div>

          {availableProviders.length === 0 ? (
            <div className="text-center py-3 px-3 bg-slate-50 border border-slate-200 rounded-lg text-[12px] font-normal text-slate-400">
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
                    className="group relative flex items-center gap-2 p-2 rounded-lg bg-white hover:bg-slate-50 border border-slate-200/60 hover:border-slate-300 transition-all duration-150 cursor-pointer active:scale-[0.98]"
                    title={`Click to manage ${cleanedName}`}
                  >
                    <div
                      className={`w-6 h-6 rounded-md flex items-center justify-center font-semibold text-[11px] shrink-0 ${
                        isConnected ? `${theme.bg} ${theme.fg}` : 'bg-slate-100 text-slate-400'
                      }`}
                    >
                      {providerLetter(server.label, server.id)}
                    </div>
                    <div className="min-w-0 flex-1 flex flex-col justify-center">
                      <span className="text-[12px] font-medium text-slate-700 truncate">
                        {cleanedName}
                      </span>
                      <div className="flex items-center gap-1">
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${
                            isConnected ? 'bg-emerald-500' : 'bg-slate-300'
                          }`}
                        />
                        <span className="text-[10px] font-normal text-slate-400 select-none">
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
      <div className="shrink-0 border-t border-slate-200 px-3 pt-3 pb-[calc(var(--safe-bottom)+0.75rem)] flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0 flex-1">
          <div className="w-7 h-7 rounded-lg bg-slate-900 text-white flex items-center justify-center font-medium text-[12px] shrink-0">
            {(username || 'C').charAt(0).toUpperCase()}
          </div>
          <span className="text-[13px] font-medium text-slate-700 truncate capitalize">
            {username || 'omi'}
          </span>
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          {isAdmin && onOpenAdmin && (
            <button
              onClick={onOpenAdmin}
              className="p-1.5 rounded-lg text-slate-400 hover:text-amber-600 hover:bg-amber-50 transition-colors cursor-pointer"
              title="Admin dashboard"
              aria-label="Admin dashboard"
            >
              <Shield size={14} />
            </button>
          )}
          <button
            onClick={onOpenSettings}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors cursor-pointer"
            title="Settings"
            aria-label="Open settings"
          >
            <Settings size={14} />
          </button>
          <button
            onClick={onLogout}
            className="p-1.5 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-colors cursor-pointer"
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
