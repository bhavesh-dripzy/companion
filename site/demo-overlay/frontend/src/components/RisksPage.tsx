import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Copy,
  ExternalLink,
  Monitor,
  Search,
  ShieldAlert,
  X,
} from "lucide-react";
import { useState } from "react";
import { useRiskOverview } from "../hooks/useRiskOverview";
import type { RiskFilters } from "../hooks/useRisks";
import type { LiveFinding, RiskSummary } from "../types";
import { RiskDetailWorkspace } from "./RiskDetailWorkspace";

const NOOP = () => undefined;

type Props = {
  clearFilters: () => void;
  filters: RiskFilters;
  findings: LiveFinding[];
  hasNext: boolean;
  hasPrevious: boolean;
  loading: string;
  nextPage: () => Promise<void>;
  offset: number;
  onAuthExpired: () => void;
  previousPage: () => Promise<void>;
  scope: string | null;
  summary: RiskSummary;
  token: string | null;
  total: number;
  updateFilters: (patch: Partial<RiskFilters>) => void;
};

export function RisksPage({
  clearFilters,
  filters,
  findings,
  hasNext,
  hasPrevious,
  loading,
  nextPage,
  offset,
  onAuthExpired,
  previousPage,
  scope,
  summary,
  token,
  total,
  updateFilters,
}: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = findings.find((finding) => findingKey(finding) === selectedId) ?? null;
  const dirty = Boolean(filters.query || filters.severity !== "all" || filters.status !== "all" || filters.priorityOnly || filters.showTransportSecurity);

  return (
    <div className="page risks-page">
      <div className="rk-subbar risks-heading">
        <div>
          <h1 className="npage-title">Risks</h1>
          <p className="npage-sub">Security findings across {scope ? scope : "all targets"}, prioritized by severity and recency.</p>
        </div>
        <button className="btn btn-ghost" onClick={() => exportRisks(findings)} disabled={!findings.length} type="button">Export current page</button>
      </div>

      <div className="rk-kpis risks-kpis">
        <Kpi icon={<ShieldAlert size={18} />} tint="orange" value={summary.open} label="Open Risks" note="Needs attention" />
        <Kpi icon={<AlertTriangle size={18} />} tint="red" value={summary.critical_high} label="Critical & High" note="Fix these first" />
        <Kpi icon={<Monitor size={18} />} tint="teal" value={summary.assets_affected} label="Assets Affected" note="Unique exposed assets" />
        <Kpi icon={<Clock3 size={18} />} tint="blue" value={summary.new_this_week} label="New This Week" note="First seen in 7 days" />
      </div>

      <div className="rk-filterbar risks-filters">
        <label className="rk-search"><Search size={14} /><input aria-label="Search risks" onChange={(event) => updateFilters({ query: event.target.value })} placeholder="Search risk, host, rule, or port..." value={filters.query} /></label>
        <FilterSelect label="Severity" value={filters.severity} onChange={(value) => updateFilters({ severity: value })} options={[["all", "All severities"], ["critical", "Critical"], ["high", "High"], ["medium", "Medium"], ["low", "Low"], ["info", "Info"], ["unknown", "Unknown"]]} />
        <FilterSelect label="Status" value={filters.status} onChange={(value) => updateFilters({ status: value })} options={[["all", "All statuses"], ["open", "Open"], ["triage", "Needs review"], ["remediated", "Remediated"]]} />
        <button className={`btn ${filters.priorityOnly ? "assets-filter-active" : ""}`} onClick={() => updateFilters({ priorityOnly: !filters.priorityOnly })} type="button">Critical & high only</button>
        <button
          aria-pressed={filters.showTransportSecurity}
          className={`btn ${filters.showTransportSecurity ? "assets-filter-active" : ""}`}
          onClick={() => {
            setSelectedId(null);
            updateFilters({ showTransportSecurity: !filters.showTransportSecurity });
          }}
          type="button"
        >
          {filters.showTransportSecurity ? "Hide TLS/SSL findings" : "Show TLS/SSL findings"}
        </button>
        {dirty ? <button className="rk-clear" onClick={clearFilters} type="button">Clear all</button> : null}
      </div>

      <div className="rk-subbar risks-table-toolbar">
        <div className="rk-count">{total ? `${offset + 1}–${Math.min(offset + findings.length, total)} of ${total}` : "0"} risks <span className="dim">· {loading}</span></div>
        <div className="rk-subbar-right">
          <FilterSelect label="Sort" value={filters.sort} onChange={(value) => updateFilters({ sort: value as RiskFilters["sort"] })} options={[["severity", "Most severe first"], ["latest", "Latest first"], ["oldest", "Oldest first"], ["host", "Asset name"]]} />
        </div>
      </div>

      <div className="rk-table-card">
        <div className="rk-table-scroll">
          <table className="rk-table risks-table">
            <thead><tr><th>Severity</th><th>Risk</th><th>Where</th><th>Status</th><th>Last Seen</th><th aria-label="Open details" /></tr></thead>
            <tbody>{findings.map((finding) => <RiskRow finding={finding} key={findingKey(finding)} onOpen={() => setSelectedId(findingKey(finding))} selected={selectedId === findingKey(finding)} />)}</tbody>
          </table>
          {!findings.length ? <div className="assets-empty"><strong>{loading.startsWith("loading") ? "Loading risks" : "No risks match this view"}</strong><span>{dirty ? "Clear the filters to see more stored findings." : "No stored SQL findings are available for this target scope."}</span></div> : null}
        </div>
        {total ? <Pager hasNext={hasNext} hasPrevious={hasPrevious} offset={offset} pageSize={filters.pageSize} total={total} onNext={() => void nextPage()} onPageSize={(pageSize) => updateFilters({ pageSize })} onPrevious={() => void previousPage()} /> : null}
      </div>

      {selected ? <RiskDetail finding={selected} onAuthExpired={onAuthExpired} onClose={() => setSelectedId(null)} token={token} /> : null}
    </div>
  );
}

function RiskRow({ finding, onOpen, selected }: { finding: LiveFinding; onOpen: () => void; selected: boolean }) {
  const severity = normalizedSeverity(finding);
  const status = normalizedStatus(finding);
  const dotTone = status === "remediated" ? "remediated" : status === "triage" ? "triage" : severity;
  return <tr aria-selected={selected} onClick={onOpen}>
    <td><span className={`badge risks-severity risks-severity-${severity}`}>{label(severity)}</span></td>
    <td><div className="rk-title">{findingTitle(finding)}</div><div className="rk-meta">{finding.target_domain ? <span className="badge badge-outline">{finding.target_domain}</span> : null}</div></td>
    <td><div className="rk-host mono">{finding.host}</div><div className="rk-service">{finding.port ? `${finding.port}/tcp` : "Asset-level finding"}</div></td>
    <td><span className={`rk-status risks-status risks-status-${status}`}><span className={`as-dot risks-status-dot risks-status-dot-${dotTone}`} />{statusLabel(status)}</span></td>
    <td className="rk-lastchecked">{dateLabel(finding.last_seen_at)}</td>
    <td style={{ textAlign: "right" }}><ChevronRight className="rk-chev" size={15} /></td>
  </tr>;
}

export function RiskDetail({ finding, onAuthExpired = NOOP, onClose, token = null }: { finding: LiveFinding; onAuthExpired?: () => void; onClose: () => void; token?: string | null }) {
  const severity = normalizedSeverity(finding);
  const status = normalizedStatus(finding);
  const findingId = finding.finding_id ?? null;
  const { overview, state: overviewState } = useRiskOverview(findingId, token, onAuthExpired);
  const displayedTitle = overview?.status === "ready" ? overview.title : findingTitle(finding);
  return <>
    <button aria-label="Close risk details" className="scrim assets-drawer-scrim" onClick={onClose} />
    <aside aria-label="Risk details" className="drawer assets-drawer risks-drawer" role="dialog" aria-modal="true">
      <header className="drawer-head assets-drawer-head">
        <div className="assets-drawer-topline">
          <div className="assets-drawer-chips"><span className={`badge risks-severity risks-severity-${severity}`}>{label(severity)}</span><span className="badge badge-outline">{statusLabel(status)}</span>{finding.target_domain ? <span className="badge badge-outline">{finding.target_domain}</span> : null}</div>
          <button aria-label="Close risk details" className="nicon" onClick={onClose}><X size={17} /></button>
        </div>
        <div className="assets-drawer-title-row">
          <div className="assets-drawer-title"><span className="drawer-eyebrow">Risk details</span><h2 className="adr-title">{displayedTitle}</h2><p>Stored security evidence from the latest completed scan.</p></div>
          <div className="adr-actions"><button className="btn" onClick={() => void navigator.clipboard.writeText(finding.host)} type="button"><Copy size={13} /> Copy host</button><a className="btn" href={`https://${finding.host}`} rel="noreferrer" target="_blank"><ExternalLink size={13} /> Open asset</a></div>
        </div>
        <div className="assets-drawer-meta"><Meta label="Severity" value={label(severity)} /><Meta label="Status" value={statusLabel(status)} /><Meta label="Service" value={finding.port ? `${finding.port}/tcp` : "Asset level"} mono /><Meta label="Last seen" value={dateLabel(finding.last_seen_at)} /></div>
      </header>
      <RiskDetailWorkspace finding={finding} findingId={findingId} onAuthExpired={onAuthExpired} overview={overview} overviewState={overviewState} severity={severity} status={status} token={token} />
    </aside>
  </>;
}

function Meta({ label: name, value, mono = false }: { label: string; value: string; mono?: boolean }) { return <div><span>{name}</span><strong className={mono ? "mono" : undefined}>{value}</strong></div>; }
function Kpi({ icon, tint, value, label: name, note }: { icon: React.ReactNode; tint: string; value: number; label: string; note: string }) { return <div className={`rk-kpi rk-kpi-${tint}`}><span className="rk-kpi-icon" style={{ background: `var(--tint-${tint})`, color: `var(--tint-${tint}-ink)` }}>{icon}</span><div className="rk-kpi-body"><div className="rk-kpi-value">{value}</div><div className="rk-kpi-label">{name}</div><div className="rk-kpi-delta rk-kpi-delta-flat">- {note}</div></div></div>; }
function FilterSelect({ label: name, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: [string, string][] }) { return <label className="assets-select"><span className="sr-only">{name}</span><select aria-label={name} value={value} onChange={(event) => onChange(event.target.value)}>{options.map(([key, text]) => <option value={key} key={key}>{text}</option>)}</select></label>; }
function Pager({ hasNext, hasPrevious, offset, pageSize, total, onNext, onPageSize, onPrevious }: { hasNext: boolean; hasPrevious: boolean; offset: number; pageSize: number; total: number; onNext: () => void; onPageSize: (size: number) => void; onPrevious: () => void }) { const page = Math.floor(offset / pageSize) + 1; const pages = Math.max(1, Math.ceil(total / pageSize)); const start = offset + 1; const end = Math.min(total, offset + pageSize); return <div className="rk-pager"><label className="rk-pager-size">Rows per page <select value={pageSize} onChange={(event) => onPageSize(Number(event.target.value))}><option value={25}>25</option><option value={50}>50</option><option value={100}>100</option></select></label><span>{start}–{end} of {total}</span><div className="rk-pager-nav"><button className="rk-pager-btn" disabled={!hasPrevious} onClick={onPrevious}><ChevronLeft size={14} /></button><span className="risks-page-number">{page} / {pages}</span><button className="rk-pager-btn" disabled={!hasNext} onClick={onNext}><ChevronRight size={14} /></button></div></div>; }

function normalizedSeverity(finding: LiveFinding) { return String(finding.severity ?? prioritySeverity(finding.priority)).toLowerCase(); }
function normalizedStatus(finding: LiveFinding) { const value = String(finding.risk_status ?? "open").toLowerCase(); return ["open", "triage", "remediated"].includes(value) ? value : "open"; }
function findingKey(finding: LiveFinding) { return finding.finding_id ?? `${finding.target_domain}:${finding.risk_id}:${finding.host}:${finding.port}`; }
export function findingTitle(finding: LiveFinding) { const value = finding.title?.trim() || finding.template_id?.trim() || "Finding metadata pending"; return value.split(/[\s_:/-]+/).filter(Boolean).map((word) => word.length <= 4 && word === word.toUpperCase() ? word : word.charAt(0).toUpperCase() + word.slice(1)).join(" "); }
function prioritySeverity(priority: number) { return priority >= 5 ? "critical" : priority >= 4 ? "high" : priority >= 3 ? "medium" : priority >= 2 ? "low" : priority >= 1 ? "info" : "unknown"; }
function statusLabel(status: string) { return status === "triage" ? "Needs review" : status === "remediated" ? "Remediated" : "Open"; }
function label(value: string) { return value.charAt(0).toUpperCase() + value.slice(1); }
function dateLabel(value?: number) { if (!value) return "Not recorded"; return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value)); }
function exportRisks(findings: LiveFinding[]) { const rows = [["severity", "risk", "status", "host", "port", "target", "last_seen", "evidence"], ...findings.map((finding) => [normalizedSeverity(finding), findingTitle(finding), normalizedStatus(finding), finding.host, String(finding.port || ""), finding.target_domain ?? "", dateLabel(finding.last_seen_at), finding.evidence_s3_url ?? finding.url ?? ""])]; const csv = rows.map((row) => row.map((cell) => `"${cell.replaceAll('"', '""')}"`).join(",")).join("\n"); const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); const link = document.createElement("a"); link.href = url; link.download = "vaayu-demo-risks.csv"; link.click(); URL.revokeObjectURL(url); }
