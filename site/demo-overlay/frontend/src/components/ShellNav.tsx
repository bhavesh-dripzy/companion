import type { ComponentType } from "react";
import { Globe, LineChart, Shield } from "lucide-react";
import type { Pane } from "../types";

function HomeIcon({ size = 19 }: { size?: string | number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /><path d="M9.5 21v-6h5v6" /></svg>;
}

type IconComponent = ComponentType<{ size?: string | number }>;
const ITEMS: { id: Pane; label: string; icon: IconComponent }[] = [
  { id: "overview", label: "Overview", icon: HomeIcon },
  { id: "findings", label: "Risks", icon: Shield },
  { id: "exposure", label: "Assets", icon: Globe },
  { id: "reports", label: "Reports", icon: LineChart },
];

export function ShellNav({ open, pane, onPane }: { open: boolean; pane: Pane; onPane: (pane: Pane) => void }) {
  return <div className="rail-nav">{ITEMS.map((item) => <button key={item.id} disabled={item.id === "reports"} className="rail-item" aria-current={pane === item.id ? "page" : undefined} onClick={() => onPane(item.id)} title={open ? undefined : item.label} type="button"><item.icon size={19} />{open ? item.label : null}</button>)}</div>;
}
