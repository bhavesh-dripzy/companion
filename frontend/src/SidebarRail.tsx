import React, { useEffect, useState } from 'react';
import { Plus, Search, Compass, Settings as SettingsIcon, Clock, Sparkles, LogOut } from 'lucide-react';

interface SidebarRailProps {
  onNewChat: () => void;
  onToggleSearch: () => void;
  onToggleExplore: () => void;
  onOpenSettings: () => void;
  onLogout: () => void;
  searchActive: boolean;
  exploreActive: boolean;
}

// The SidebarRail is the slim icon column on the left of the workspace.
// It bundles together every workspace-level command — start a fresh
// conversation, search through the current chat, surface the
// quick-action prompt shelf, open Settings (where MCP connect/disconnect
// flows live), and sign out. No backend feature was removed in the
// re-skin; the rail just consolidates entry points that previously
// lived on the top header.
export const SidebarRail: React.FC<SidebarRailProps> = ({
  onNewChat,
  onToggleSearch,
  onToggleExplore,
  onOpenSettings,
  onLogout,
  searchActive,
  exploreActive,
}) => {
  const [timeStr, setTimeStr] = useState('');

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setTimeStr(now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  const railButton = (active: boolean) =>
    `w-10 h-10 rounded-full flex items-center justify-center transition-all duration-200 active:scale-95 cursor-pointer ${
      active
        ? 'text-slate-900 bg-slate-900/10 ring-1 ring-slate-900/15'
        : 'text-slate-600 hover:text-slate-900 bg-slate-100/60 hover:bg-slate-100'
    }`;

  return (
    <div
      id="sidebar-rail"
      className="w-18 bg-white/40 backdrop-blur-xl border-r border-slate-200/60 flex flex-col items-center py-6 justify-between shrink-0 select-none z-10"
    >
      <div className="flex flex-col items-center gap-5 w-full">
        <button
          onClick={onNewChat}
          title="New conversation"
          className="w-11 h-11 bg-slate-950 hover:bg-slate-800 text-white rounded-full flex items-center justify-center transition-all duration-300 hover:scale-105 active:scale-95 shadow-lg shadow-black/10 cursor-pointer"
        >
          <Plus size={20} strokeWidth={2.5} />
        </button>

        <div className="w-8 h-[1px] bg-slate-200/70" />

        <button
          onClick={onToggleSearch}
          title="Search this conversation"
          className={railButton(searchActive)}
        >
          <Search size={18} />
        </button>

        <button
          onClick={onToggleExplore}
          title="Explore prompt ideas"
          className={railButton(exploreActive)}
        >
          <Compass size={18} />
        </button>

        <button
          onClick={onOpenSettings}
          title="Open Settings · MCP & integrations"
          className={railButton(false)}
        >
          <SettingsIcon size={18} />
        </button>
      </div>

      <div className="flex flex-col items-center gap-5">
        <button
          onClick={onLogout}
          title="Sign out"
          className="w-10 h-10 text-slate-500 hover:text-red-600 bg-slate-100/60 hover:bg-red-50 rounded-full flex items-center justify-center transition-all duration-200 active:scale-95 cursor-pointer"
        >
          <LogOut size={17} />
        </button>

        <div
          className="flex flex-col items-center text-center group cursor-help"
          title={`Local time: ${timeStr}`}
        >
          <div className="w-10 h-10 text-slate-600 hover:text-slate-900 bg-slate-100/60 hover:bg-slate-100 rounded-full flex items-center justify-center transition-all duration-200">
            <Clock size={18} className="group-hover:animate-spin-slow" />
          </div>
          <span className="text-[10px] font-mono font-bold text-slate-500 mt-1 scale-90">
            {timeStr.slice(0, 5)}
          </span>
        </div>

        <div
          className="w-11 h-11 bg-slate-950 text-white rounded-full flex items-center justify-center font-bold tracking-tight shadow hover:scale-105 transition-all duration-200"
          title="Companion"
        >
          <div className="w-5 h-5 flex items-center justify-center border-2 border-dashed border-white rounded-full animate-spin-slow">
            <Sparkles size={11} className="text-amber-300" />
          </div>
        </div>
      </div>
    </div>
  );
};
