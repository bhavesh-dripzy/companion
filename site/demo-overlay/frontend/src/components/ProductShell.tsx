import { ChevronDown, ChevronLeft, ChevronRight, LogOut, RefreshCw, Settings } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { Pane } from "../types";
import { ShellNav } from "./ShellNav";

type Props = { activeRuns: number; apiState: string; children: ReactNode; onLogout: () => void; onPane: (pane: Pane) => void; onRefresh: () => void; onSearch: (query: string) => void; pane: Pane; searchQuery: string; };
type Menu = "profile" | null;

export function ProductShell(props: Props) {
  const [railOpen, setRailOpen] = useState(() => typeof window === "undefined" ? true : window.matchMedia("(min-width: 1024px)").matches);
  const [menu, setMenu] = useState<Menu>(null);
  return (
    <main className="shell" data-rail={railOpen ? "open" : "closed"}>
      <nav aria-label="Primary" className="rail" data-open={railOpen}>
        <div className="rail-brand">
          <span className="rail-logo rail-logo-vaayu"><img alt={railOpen ? "Vaayu" : ""} src="./vaayu-logo.png" /></span>
        </div>
        <ShellNav open={railOpen} onPane={props.onPane} pane={props.pane} />
        <div style={{ marginTop: "auto" }}>
          <div className="rail-foot"><button aria-current={props.pane === "settings" ? "page" : undefined} className="rail-item" disabled title="Settings are not available in this demo" onClick={() => props.onPane("settings")} type="button"><Settings size={19} />{railOpen ? "Settings" : null}</button><button className="rail-item" onClick={() => setRailOpen((open) => !open)} type="button">{railOpen ? <ChevronLeft size={19} /> : <ChevronRight size={19} />}{railOpen ? "Collapse" : null}</button></div>
        </div>
      </nav>
      <section className="main">
        <header className="topbar">
          <div className="topbar-right">
            <button className="nicon" onClick={props.onRefresh} title="Refresh data" aria-label="Refresh data"><RefreshCw size={16} /></button>
            <MenuWrap open={menu === "profile"}><button className="navatar" onClick={() => setMenu(menu === "profile" ? null : "profile")} type="button"><span className="navatar-mark">FO</span><span className="navatar-text"><b>Demo workspace</b><small>{props.activeRuns ? `${props.activeRuns} active scans` : "Administrator"}</small></span><ChevronDown className="navatar-caret" size={14} /></button><MenuSurface><button className="pop-item" disabled title="Settings are not available in this demo" onClick={() => props.onPane("settings")} type="button">Settings</button><button className="pop-item" disabled title="This fictional workspace has no signed-in account" onClick={props.onLogout} type="button"><LogOut size={13} /> Sign out</button></MenuSurface></MenuWrap>
          </div>
        </header>
        <div className={`viewport workspace-${props.pane}`}>{props.children}</div>
      </section>
    </main>
  );
}

function MenuWrap({ children, open }: { children: ReactNode; open: boolean }) { return <div className="rk-dd-wrap prototype-menu" data-open={open}>{children}</div>; }
function MenuSurface({ children }: { children: ReactNode }) { return <div className="pop prototype-menu-surface">{children}</div>; }
