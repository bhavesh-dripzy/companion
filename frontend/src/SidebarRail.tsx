import React, { useEffect, useState } from 'react';
import {
  Plus,
  Search,
  Compass,
  Settings as SettingsIcon,
  Clock,
  Sparkles,
  LogOut,
  X,
  Database,
  Trash2,
  MessageSquare,
} from 'lucide-react';
import type { ChatSummary } from './types';

interface SidebarRailProps {
  onNewChat: () => void;
  onToggleSearch: () => void;
  onToggleExplore: () => void;
  onOpenSettings: () => void;
  onLogout: () => void;
  searchActive: boolean;
  exploreActive: boolean;
  // Mobile drawer controls. When `isMobileOpen` is true the rail slides
  // in over the chat with a tap-to-close backdrop; on `md` and up the
  // rail is always visible and these props are ignored.
  isMobileOpen: boolean;
  onMobileClose: () => void;
  isAdmin?: boolean;
  onOpenAdmin?: () => void;
  // Chat-history controls. `chats` is the user's saved chats (sidebar
  // list — most-recent-first), `activeChatId` highlights the one
  // currently open in the workspace, `onSelectChat` switches to another
  // chat (parent fetches its messages), and `onDeleteChat` removes
  // a chat row + its messages from Dynamo.
  chats: ChatSummary[];
  activeChatId: string | null;
  onSelectChat: (chatId: string) => void;
  onDeleteChat: (chatId: string) => void;
}

// The SidebarRail combines the workspace-level command icons (new chat,
// search, explore, settings, sign out) with an inline chat-history
// panel. Each saved chat is rendered as a clickable row — clicking
// switches the workspace to that conversation; the trash icon deletes
// the chat (metadata + messages) after a quick confirm.
//
// The chat list is the single source of truth for "which conversation
// am I in" in the UI — the parent persists the active chat_id in
// localStorage so a reload restores the same conversation.
export const SidebarRail: React.FC<SidebarRailProps> = ({
  onNewChat,
  onToggleSearch,
  onToggleExplore,
  onOpenSettings,
  onLogout,
  searchActive,
  exploreActive,
  isMobileOpen,
  onMobileClose,
  isAdmin,
  onOpenAdmin,
  chats,
  activeChatId,
  onSelectChat,
  onDeleteChat,
}) => {
  const [timeStr, setTimeStr] = useState('');
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setTimeStr(now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  // Reset the pending-delete confirm whenever the chat list changes
  // (e.g. the delete went through and the row vanished, or the user
  // switched chats — they almost certainly meant to cancel).
  useEffect(() => {
    if (pendingDeleteId && !chats.some((c) => c.chat_id === pendingDeleteId)) {
      setPendingDeleteId(null);
    }
  }, [chats, pendingDeleteId]);

  const iconBtn = (active: boolean) =>
    `w-9 h-9 rounded-full flex items-center justify-center transition-all duration-200 active:scale-95 cursor-pointer ${
      active
        ? 'text-slate-900 bg-slate-900/10 ring-1 ring-slate-900/15'
        : 'text-slate-600 hover:text-slate-900 bg-slate-100/60 hover:bg-slate-100'
    }`;

  // Each rail action auto-closes the mobile drawer so the chat is
  // visible again immediately after the tap. On `md` and up the close
  // is a no-op (the drawer isn't open in the first place).
  const tap = (fn: () => void) => () => {
    fn();
    onMobileClose();
  };

  const handleChatClick = (chatId: string) => {
    if (pendingDeleteId === chatId) return;
    onSelectChat(chatId);
    onMobileClose();
  };

  const handleDeleteClick = (e: React.MouseEvent, chatId: string) => {
    e.stopPropagation();
    if (pendingDeleteId === chatId) {
      onDeleteChat(chatId);
      setPendingDeleteId(null);
    } else {
      setPendingDeleteId(chatId);
    }
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
        id="sidebar-rail"
        className={`bg-white/85 md:bg-white/40 backdrop-blur-xl border-r border-slate-200/60 flex flex-col shrink-0 select-none transition-transform duration-300 ease-out ${
          // Mobile: fixed slide-in drawer. Desktop: in-flow column with
          // enough width for the chat list to be readable.
          'fixed md:static inset-y-0 left-0 z-40 w-72 md:w-64 pt-[var(--safe-top)] md:pt-0'
        } ${
          isMobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'
        }`}
      >
        <button
          onClick={onMobileClose}
          className="md:hidden absolute -right-3 top-3 w-7 h-7 bg-white border border-slate-200 rounded-full flex items-center justify-center shadow-sm text-slate-600 hover:text-slate-900"
          title="Close menu"
          aria-label="Close menu"
        >
          <X size={14} />
        </button>

        {/* Top branding row */}
        <div className="px-4 pt-5 pb-3 flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-8 h-8 bg-slate-950 text-white rounded-full flex items-center justify-center shadow shrink-0">
              <Sparkles size={13} className="text-amber-300" />
            </div>
            <span className="text-sm font-extrabold tracking-tight text-slate-900 truncate">
              Companion
            </span>
          </div>
        </div>

        {/* New chat — primary CTA, full-width pill so it's the obvious
            next action when the user wants a clean slate. */}
        <div className="px-3">
          <button
            onClick={tap(onNewChat)}
            title="New conversation"
            className="w-full flex items-center justify-center gap-2 bg-slate-950 hover:bg-slate-800 text-white text-sm font-semibold rounded-xl py-2.5 transition-all duration-200 hover:scale-[1.01] active:scale-[0.99] shadow-md shadow-black/10 cursor-pointer"
          >
            <Plus size={16} strokeWidth={2.5} />
            New chat
          </button>
        </div>

        {/* Quick-action icon row — search / explore / settings / admin */}
        <div className="px-3 mt-3 mb-2 flex items-center gap-2">
          <button
            onClick={tap(onToggleSearch)}
            title="Search this conversation"
            className={iconBtn(searchActive)}
          >
            <Search size={16} />
          </button>
          <button
            onClick={tap(onToggleExplore)}
            title="Explore prompt ideas"
            className={iconBtn(exploreActive)}
          >
            <Compass size={16} />
          </button>
          <button
            onClick={tap(onOpenSettings)}
            title="Open Settings · MCP & integrations"
            className={iconBtn(false)}
          >
            <SettingsIcon size={16} />
          </button>
          {isAdmin && onOpenAdmin && (
            <button
              onClick={tap(onOpenAdmin)}
              title="Open Admin Dashboard"
              className={iconBtn(false)}
            >
              <Database size={16} className="text-amber-500" />
            </button>
          )}
        </div>

        {/* Chat history — the scrollable list of saved chats. Shows a
            friendly empty state when the user has never sent a turn. */}
        <div className="px-4 pt-3 pb-1 flex items-center gap-1.5">
          <MessageSquare size={11} className="text-slate-400" />
          <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
            Chats {chats.length > 0 && `(${chats.length})`}
          </span>
        </div>

        <div className="flex-1 overflow-y-auto px-2 pb-2 [scrollbar-width:thin] [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-slate-300 [&::-webkit-scrollbar-thumb]:rounded-full">
          {chats.length === 0 ? (
            <div className="px-3 py-4 text-[11px] text-slate-400 italic leading-relaxed">
              No chats yet — your conversations will appear here once you start one.
            </div>
          ) : (
            <div className="flex flex-col gap-0.5">
              {chats.map((c) => {
                const active = c.chat_id === activeChatId;
                const isPendingDelete = pendingDeleteId === c.chat_id;
                return (
                  <button
                    key={c.chat_id}
                    onClick={() => handleChatClick(c.chat_id)}
                    className={`group w-full text-left px-2.5 py-2 rounded-lg flex items-start gap-1.5 transition-all duration-150 cursor-pointer ${
                      active
                        ? 'bg-slate-900/10 ring-1 ring-slate-900/15'
                        : 'hover:bg-slate-100/70'
                    }`}
                    title={c.title}
                  >
                    <div className="flex-1 min-w-0">
                      <div className={`text-[12px] font-semibold leading-snug truncate ${
                        active ? 'text-slate-900' : 'text-slate-700'
                      }`}>
                        {c.title || 'New chat'}
                      </div>
                      {c.last_message_preview && (
                        <div className="text-[10px] text-slate-400 mt-0.5 line-clamp-1 leading-tight">
                          {c.last_message_preview}
                        </div>
                      )}
                    </div>
                    <span
                      onClick={(e) => handleDeleteClick(e, c.chat_id)}
                      className={`shrink-0 mt-0.5 w-6 h-6 rounded-md flex items-center justify-center transition-all duration-150 ${
                        isPendingDelete
                          ? 'bg-red-500 text-white'
                          : 'opacity-0 group-hover:opacity-100 text-slate-400 hover:bg-red-50 hover:text-red-600'
                      }`}
                      title={isPendingDelete ? 'Click again to confirm delete' : 'Delete chat'}
                      role="button"
                      aria-label={isPendingDelete ? 'Confirm delete chat' : 'Delete chat'}
                    >
                      <Trash2 size={12} />
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer — sign out + local clock. Always pinned to the bottom
            so the chat list above can scroll independently. */}
        <div className="px-3 py-3 border-t border-slate-200/60 flex items-center justify-between gap-2 shrink-0">
          <button
            onClick={tap(onLogout)}
            title="Sign out"
            className="w-9 h-9 text-slate-500 hover:text-red-600 bg-slate-100/60 hover:bg-red-50 rounded-full flex items-center justify-center transition-all duration-200 active:scale-95 cursor-pointer"
          >
            <LogOut size={15} />
          </button>
          <div
            className="flex items-center gap-1.5 text-slate-500 cursor-help"
            title={`Local time: ${timeStr}`}
          >
            <Clock size={12} className="text-slate-400" />
            <span className="text-[11px] font-mono font-bold tracking-wide">
              {timeStr.slice(0, 5)}
            </span>
          </div>
        </div>
      </div>
    </>
  );
};
