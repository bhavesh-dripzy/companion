import {
  Braces, CheckCircle2, ChevronLeft, ChevronRight, Copy, Download, ExternalLink,
  EllipsisVertical, Globe, Link2, LoaderCircle, Search, Server, ShieldAlert, X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ExposureAsset } from "../exposureModel";
import type { AssetFilterFacets, AssetFilters, AssetSummary, LiveFinding, SurfaceEndpoint, SurfaceParameter, SurfaceService } from "../types";
import { AssetRiskRows, EndpointRows, ParameterRows, ServiceRows } from "./AssetDetailRows";
import { RiskDetail } from "./RisksPage";

type Props = {
  assets: ExposureAsset[];
  assetCursor: string | null;
  assetHasPrevious: boolean;
  assetOffset: number;
  assetTotal: number;
  loading: string;
  onLoadAssetDetail: (host: string) => Promise<{ endpoints: SurfaceEndpoint[]; findings: LiveFinding[]; parameters: SurfaceParameter[]; services: SurfaceService[] }>;
  onNextAssets: () => void;
  onPreviousAssets: () => void;
  onUpdateAssetReview: (assetId: string, reviewStatus: "pending" | "accepted" | "rejected") => Promise<void>;
  facets: AssetFilterFacets;
  filters: AssetFilters;
  onFilters: (filters: AssetFilters) => void;
  summary: AssetSummary;
};

export function PrototypeAssets({ assets, assetCursor, assetHasPrevious, assetOffset, assetTotal, facets, filters, loading, onFilters, onLoadAssetDetail, onNextAssets, onPreviousAssets, onUpdateAssetReview, summary }: Props) {
  const { port, query, vulnerable: onlyRisks } = filters;
  const updateFilters = (next: Partial<AssetFilters>) => onFilters({ ...filters, ...next });
  const [selectedHost, setSelectedHost] = useState<string | null>(null);

  const ports = useMemo(() => facets.ports.length ? facets.ports : [...new Set(assets.flatMap((asset) => asset.ports))].sort((a, b) => a - b), [assets, facets.ports]);
  const rows = assets;
  const selected = assets.find((asset) => asset.host === selectedHost) ?? null;
  const acceptedCount = Number(summary.accepted ?? 0);
  const clear = () => onFilters({ query: "", reachability: "resolved", port: "all", vulnerable: false });
  const dirty = Boolean(query || port !== "all" || onlyRisks);

  return (
    <div className="page">
      <div className="rk-subbar assets-heading">
        <div><h1 className="npage-title">Assets</h1><p className="npage-sub">Everything currently stored for your external perimeter - {assetTotal} assets discovered.</p></div>
      </div>

      <div className="rk-filterbar assets-filters assets-reference-filters">
        <label className="rk-search"><Search size={14} /><input aria-label="Search assets" onChange={(event) => updateFilters({ query: event.target.value })} placeholder="Search by asset, domain, or IP..." value={query} /></label>
        <FilterSelect label="Open port" value={port} onChange={(value) => updateFilters({ port: value })} options={[["all", "All ports"], ...ports.map((value) => [String(value), String(value)] as [string,string])]} />
        <button className={`btn assets-vulnerable-filter ${onlyRisks ? "assets-filter-active" : ""}`} onClick={() => updateFilters({ vulnerable: !onlyRisks })} type="button"><ShieldAlert size={14} />Only vulnerable</button>
        {dirty ? <button className="rk-clear" onClick={clear} type="button">Clear all</button> : null}
        <span className="assets-filter-spacer" />
        <button className="btn btn-ghost" onClick={() => exportAssets(rows)} type="button"><Download size={13} /> Export</button>
        <button aria-label="More asset actions" className="btn btn-icon" type="button"><EllipsisVertical size={15} /></button>
      </div>

      <div className="rk-subbar assets-table-toolbar">
        <div className="rk-count">{assetTotal ? `${assetOffset + 1}–${assetOffset + rows.length}` : "0"} of {assetTotal} assets <span className="assets-selected-count">· {acceptedCount} accepted</span><span className="dim"> · {loading}</span></div>
      </div>

      <div className="rk-table-card">
        <div className="rk-table-scroll">
          <table className="rk-table assets-main-table">
            <thead><tr><th>Asset</th><th>Type</th><th>Address</th><th>Open risks</th><th>Risk level</th><th>Ports</th><th>Last seen</th><th className="assets-open-col">Actions</th></tr></thead>
            <tbody>{rows.map((asset) => <AssetRow asset={asset} key={asset.host} onOpen={() => setSelectedHost(asset.host)} selected={selectedHost === asset.host} />)}</tbody>
          </table>
          {!rows.length ? <div className="assets-empty"><strong>No assets match this view</strong><span>{dirty ? "Clear the filters to see the stored inventory." : "Run recon to populate the asset inventory."}</span></div> : null}
        </div>
        {rows.length ? <div className="rk-pager"><span>50 rows per page</span><span>{assetOffset + 1}–{assetOffset + rows.length} of {assetTotal}</span><div className="rk-pager-nav"><button className="rk-pager-btn" disabled={!assetHasPrevious || loading.startsWith("loading")} onClick={onPreviousAssets}><ChevronLeft size={14} /></button><button className="rk-pager-btn" disabled={!assetCursor || loading.startsWith("loading")} onClick={onNextAssets}><ChevronRight size={14} /></button></div></div> : null}
      </div>
      {selected ? <AssetDetail asset={selected} onClose={() => setSelectedHost(null)} onLoad={onLoadAssetDetail} onUpdateReview={onUpdateAssetReview} /> : null}
    </div>
  );
}

function AssetRow({ asset, onOpen, selected }: { asset: ExposureAsset; onOpen: () => void; selected: boolean }) {
  const severity = asset.highestRiskSeverity?.toLowerCase() ?? null;
  const remainingRisks = Math.max(0, asset.openRiskCount - asset.highestRiskCount);
  const type = assetKind(asset);
  return <tr aria-selected={selected} onClick={onOpen}>
    <td><span className="cell-2 assets-asset-cell"><b className="mono truncate">{asset.host}</b><small className="truncate">{asset.ips.length ? `${asset.ips.length} ${asset.ips.length === 1 ? "address" : "addresses"}` : "Not resolved"} · {asset.ports.length} open {asset.ports.length === 1 ? "port" : "ports"}</small></span></td>
    <td><AssetType type={type} /></td>
    <td>{asset.ips.length ? <span className="row-tight nowrap assets-address-line"><span className="mono assets-primary-value">{asset.ips[0]}</span><button aria-label={`Copy ${asset.ips[0]}`} className="as-copy" onClick={(event) => { event.stopPropagation(); void copyText(asset.ips[0]); }} title="Copy address" type="button"><Copy size={11} /></button>{asset.ips.length > 1 ? <span aria-label={`${asset.ips.length - 1} additional ${asset.ips.length === 2 ? "address" : "addresses"}`} className="assets-address-more" title={`${asset.ips.length - 1} additional ${asset.ips.length === 2 ? "address" : "addresses"}`}>+{asset.ips.length - 1} more</span> : null}</span> : <span className="dim">No active address</span>}</td>
    <td><span className={`assets-risk-count assets-risk-count-${severity ?? "none"}`}>{asset.openRiskCount}</span></td>
    <td><RiskLevel count={asset.highestRiskCount} remaining={remainingRisks} severity={severity} /></td>
    <td><CompactPills values={asset.ports.map(String)} empty="-" /></td>
    <td><span className="nowrap">{findingDate(asset.lastSeenAt)}</span></td>
    <td className="assets-open-col"><ChevronRight className="assets-row-chevron" size={16} /></td>
  </tr>;
}

function AssetDetail({ asset, onClose, onLoad, onUpdateReview }: { asset: ExposureAsset; onClose: () => void; onLoad: Props["onLoadAssetDetail"]; onUpdateReview: Props["onUpdateAssetReview"] }) {
  const [tab, setTab] = useState<"overview"|"services"|"endpoints"|"parameters"|"risks">("overview");
  const [selectedRisk, setSelectedRisk] = useState<LiveFinding | null>(null);
  const [detail, setDetail] = useState(asset);
  const [state, setState] = useState("Loading stored details");
  const [reviewPending, setReviewPending] = useState(false);
  useEffect(() => {
    let active = true;
    setDetail(asset);
    setState("Loading stored details");
    void onLoad(asset.host).then((surface) => {
      if (!active) return;
      const technologies = [...new Set([...asset.technologies, ...surface.services.flatMap((item) => [...(item.technologies ?? []), item.product, item.name].filter(Boolean) as string[])])].sort();
      const ports = [...new Set([...asset.ports, ...surface.services.map((item) => item.port)])].sort((left, right) => left - right);
      setDetail({ ...asset, ...surface, technologies, ports });
      setState("Latest stored scan");
    }).catch(() => { if (active) setState("Details temporarily unavailable"); });
    return () => { active = false; };
  }, [asset, onLoad]);
  const tabs = [
    ["overview", "Overview", null],
    ["services", "Services", detail.services.length],
    ["endpoints", "Pages & APIs", detail.endpoints.length],
    ["parameters", "Query Parameters", detail.parameters.length],
    ["risks", "Risks", detail.findings.length],
  ] as const;
  const accepted = detail.reviewStatus === "accepted";
  const updateReview = async () => {
    if (!detail.entityId || reviewPending) return;
    const nextStatus = accepted ? "pending" : "accepted";
    setReviewPending(true);
    try {
      await onUpdateReview(detail.entityId, nextStatus);
      setDetail((current) => ({ ...current, accepted: nextStatus === "accepted", reviewStatus: nextStatus }));
    } finally {
      setReviewPending(false);
    }
  };
  return <>
    <button aria-label="Close asset details" className="scrim assets-drawer-scrim" onClick={onClose} />
    <aside aria-label="Asset details" className="drawer assets-drawer" role="dialog" aria-modal="true">
      <header className="drawer-head assets-drawer-head">
        <div className="assets-drawer-topline">
          <div className="assets-drawer-chips"><span className={`badge ${detail.ips.length ? "badge-ok" : ""}`}>{detail.ips.length ? "Reachable" : "Not reachable"}</span><span className="badge badge-outline">Internet-facing asset</span><span className="assets-load-state">{state}</span></div>
          <button aria-label="Close asset details" className="nicon" onClick={onClose}><X size={17} /></button>
        </div>
        <div className="assets-drawer-title-row">
          <div className="assets-drawer-title"><span className="drawer-eyebrow">Asset details</span><h2 className="adr-title mono">{detail.host}</h2><p>Current evidence from the latest completed reconnaissance run.</p></div>
          <div className="adr-actions"><button aria-pressed={accepted} className={`btn assets-review-action ${accepted ? "assets-review-accepted" : ""}`} disabled={reviewPending || !detail.entityId} onClick={() => void updateReview()} title={accepted ? "Move this asset back to pending review" : "Accept this discovered asset"} type="button">{reviewPending ? <LoaderCircle className="assets-review-spinner" size={13} /> : <CheckCircle2 size={13} />} {reviewPending ? "Saving" : accepted ? "Accepted" : "Accept asset"}</button><button className="btn" onClick={() => void copyText(detail.host)} type="button"><Copy size={13} /> Copy</button><a className="btn" href={`https://${detail.host}`} rel="noreferrer" target="_blank"><ExternalLink size={13} /> Open</a></div>
        </div>
        <div className="assets-drawer-meta"><Meta label="Addresses" value={String(detail.ips.length)} /><Meta label="Open ports" value={String(detail.ports.length)} /><Meta label="Open risks" value={String(activeFindings(detail.findings).length)} /><Meta label="Software / protocols" value={String(identifiedTechnologies(detail.technologies).length)} /></div>
        <nav aria-label="Asset detail sections" className="assets-drawer-tabs">{tabs.map(([value,label,count]) => <button aria-selected={tab === value} className="tab" onClick={() => setTab(value)} key={value} type="button"><span>{label}</span>{count !== null ? <b className="tab-count">{count}</b> : null}</button>)}</nav>
      </header>
      <div className="drawer-body assets-drawer-body">
        {tab === "overview" ? <AssetOverview asset={detail} onTab={setTab} /> : null}
        {tab === "services" ? <ServiceRows services={detail.services} /> : null}
        {tab === "endpoints" ? <EndpointRows endpoints={detail.endpoints} /> : null}
        {tab === "parameters" ? <ParameterRows parameters={detail.parameters} endpoints={detail.endpoints} /> : null}
        {tab === "risks" ? <AssetRiskRows findings={detail.findings} onOpen={setSelectedRisk} /> : null}
      </div>
    </aside>
    {selectedRisk ? <RiskDetail finding={selectedRisk} token="synthetic-preview-only" onClose={() => setSelectedRisk(null)} /> : null}
  </>;
}

function AssetOverview({ asset, onTab }: { asset: ExposureAsset; onTab: (tab: "services"|"endpoints"|"parameters"|"risks") => void }) {
  const findings = activeFindings(asset.findings);
  const severity = [5,4,3,2,1].map((priority) => ({ priority, count: findings.filter((item) => item.priority === priority).length }));
  const highestPriority = Math.max(0, ...findings.map((item) => item.priority));
  const unknownServices = asset.services.filter((item) => item.identification_status !== "identified" || serviceName(item.name) === "Not identified").length;
  const technologies = identifiedTechnologies(asset.technologies);
  const responseHealth = {
    success: asset.endpoints.filter((item) => item.status !== null && item.status !== undefined && item.status >= 200 && item.status < 300).length,
    redirect: asset.endpoints.filter((item) => item.status !== null && item.status !== undefined && item.status >= 300 && item.status < 400).length,
    clientError: asset.endpoints.filter((item) => item.status !== null && item.status !== undefined && item.status >= 400 && item.status < 500).length,
    serverError: asset.endpoints.filter((item) => item.status !== null && item.status !== undefined && item.status >= 500).length,
  };
  const lastObserved = Math.max(0, ...asset.services.map((item) => item.last_seen_at ?? 0), ...asset.endpoints.map((item) => item.last_seen_at ?? 0), ...asset.findings.map((item) => item.last_seen_at ?? 0));
  return <div className="assets-detail-overview">
    <section className={`assets-overview-hero ${findings.length ? "assets-overview-hero-attention" : "assets-overview-hero-clear"}`}>
      <span className="assets-overview-hero-icon">{findings.length ? <ShieldAlert size={20} /> : <Globe size={20} />}</span>
      <div className="assets-overview-hero-copy"><span className="assets-card-kicker">Asset posture</span><h3>{findings.length ? `${findings.length} open ${findings.length === 1 ? "risk" : "risks"} recorded` : "No open risks are stored"}</h3><p>This public asset exposes {asset.services.length} {asset.services.length === 1 ? "service" : "services"} across {asset.ips.length} {asset.ips.length === 1 ? "address" : "addresses"}, with {asset.endpoints.length} discovered web {asset.endpoints.length === 1 ? "endpoint" : "endpoints"}.</p></div>
      <div className="assets-overview-hero-meta">{highestPriority ? <span className={`badge risks-severity risks-severity-${priorityLabel(highestPriority).toLowerCase()}`}>Highest: {priorityLabel(highestPriority)}</span> : <span className="badge badge-ok">No active finding</span>}<small>Last observed {findingDate(lastObserved || undefined)}</small></div>
    </section>

    <section className="assets-detail-card assets-summary-card"><div className="assets-card-heading"><div><span className="assets-card-kicker">Explore this asset</span><h3>Stored attack surface</h3></div></div><div className="assets-stat-grid"><Stat icon={<Server size={16} />} value={asset.services.length} label="Open services" onClick={() => onTab("services")} /><Stat icon={<Link2 size={16} />} value={asset.endpoints.length} label="Pages & APIs" onClick={() => onTab("endpoints")} /><Stat icon={<Braces size={16} />} value={asset.parameters.length} label="Query parameters" onClick={() => onTab("parameters")} /><Stat icon={<ShieldAlert size={16} />} value={findings.length} label="Open risks" onClick={() => onTab("risks")} /></div></section>
    <div className="assets-overview-grid">
      <section className="assets-detail-card"><div className="assets-card-heading"><h3>Network exposure</h3><span>{asset.ports.length} open ports</span></div><div className="assets-network-facts"><div><span>Addresses</span><Pills values={asset.ips} empty="No resolved addresses" /></div><div><span>Open ports</span><button className="assets-overview-link" onClick={() => onTab("services")} type="button"><span>{asset.ports.length ? asset.ports.join(", ") : "None recorded"}</span><ChevronRight size={13} /></button></div><div><span>Unidentified services</span><strong className={unknownServices ? "assets-attention-text" : ""}>{unknownServices}</strong></div></div></section>
      <section className="assets-detail-card"><div className="assets-card-heading"><h3>Web response health</h3><button className="assets-overview-link" onClick={() => onTab("endpoints")} type="button">View endpoints <ChevronRight size={13} /></button></div><div className="assets-response-grid"><ResponseMetric label="Successful" value={responseHealth.success} tone="ok" /><ResponseMetric label="Redirects" value={responseHealth.redirect} tone="redirect" /><ResponseMetric label="Client errors" value={responseHealth.clientError} tone="warn" /><ResponseMetric label="Server errors" value={responseHealth.serverError} tone="error" /></div></section>
      <section className="assets-detail-card"><div className="assets-card-heading"><h3>Software & protocols</h3><span>{technologies.length} identified</span></div><div className="assets-tech-grid">{technologies.length ? technologies.map((value) => <span className="assets-tech-item" key={value}><Globe size={14} /><span>{value}</span></span>) : <span className="dim">No software or protocol identified</span>}</div>{unknownServices ? <p className="assets-card-note">{unknownServices} open {unknownServices === 1 ? "service has" : "services have"} not been identified yet.</p> : null}</section>
      <section className="assets-detail-card"><div className="assets-card-heading"><h3>Risk distribution</h3><button className="assets-overview-link" onClick={() => onTab("risks")} type="button">View risks <ChevronRight size={13} /></button></div><div className="assets-severity-list">{severity.map(({ priority, count }) => <div key={priority}><span className={`as-dot assets-severity-dot risk-dot-${priority}`} /><span>{priorityLabel(priority)}</span><strong>{count}</strong></div>)}</div></section>
    </div>
  </div>;
}

function ResponseMetric({ label, value, tone }: { label: string; value: number; tone: "ok"|"redirect"|"warn"|"error" }) { return <div className={`assets-response-metric assets-response-${tone}`}><strong>{value}</strong><span>{label}</span></div>; }
function Meta({ label, value }: { label: string; value: string }) { return <div><span>{label}</span><strong>{value}</strong></div>; }

function FilterSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: [string,string][] }) { return <label className="assets-select"><span className="sr-only">{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}>{options.map(([key,text]) => <option value={key} key={key}>{text}</option>)}</select></label>; }
function Pills({ values, empty = "none" }: { values: string[]; empty?: string }) { return values.length ? <span className="assets-pills">{values.slice(0,3).map((value) => <span className="badge badge-outline" key={value}>{value}</span>)}{values.length > 3 ? <span className="dim">+{values.length - 3}</span> : null}</span> : <span className="dim">{empty}</span>; }
function CompactPills({ values, empty }: { values: string[]; empty: string }) { return values.length ? <span className="assets-pills">{values.slice(0,2).map((value) => <span className="badge badge-outline mono" key={value}>{value}</span>)}{values.length > 2 ? <span className="assets-address-more">+{values.length - 2}</span> : null}</span> : <span className="dim">{empty}</span>; }
function RiskLevel({ count, remaining, severity }: { count: number; remaining: number; severity: string | null }) { const label = severity ? sentenceCase(severity) : "None"; return <span className="assets-risk-summary"><span className={`assets-risk-level assets-risk-level-${severity ?? "none"}`}><i />{label}{severity ? ` · ${count}` : ""}</span>{remaining ? <span className="assets-address-more" title={`${remaining} additional lower-severity ${remaining === 1 ? "risk" : "risks"}`}>+{remaining}</span> : null}</span>; }
function AssetType({ type }: { type: ExposureAsset["assetType"] }) { if (!type) return <span className="dim">Not provided</span>; const label = type === "dns" ? "Domain" : type === "cidr" ? "Network / CIDR" : "IP Address"; return <span className={`assets-type assets-type-${type}`}><i>{type === "dns" ? <Globe size={14} /> : <Server size={14} />}</i>{label}</span>; }
function Stat({ icon, value, label, onClick }: { icon: React.ReactNode; value: number; label: string; onClick: () => void }) { return <button onClick={onClick} type="button"><i>{icon}</i><strong>{value}</strong><span>{label}</span><ChevronRight size={14} /></button>; }
function priorityLabel(priority: number) { return priority >= 5 ? "Critical" : priority >= 4 ? "High" : priority >= 3 ? "Medium" : priority >= 2 ? "Low" : "Info"; }
function activeFindings(findings: LiveFinding[]) { return findings.filter((finding) => String(finding.risk_status ?? "open").toLowerCase() !== "remediated"); }
function identifiedTechnologies(values: string[]) { return values.filter((value) => { const normalized = value.trim().toLowerCase(); return Boolean(normalized && normalized !== "unknown" && normalized !== "not identified"); }); }
function serviceName(value?: string) { const normalized = String(value ?? "").trim(); return normalized && normalized.toLowerCase() !== "unknown" ? normalized : "Not identified"; }
function assetKind(asset: ExposureAsset) { return asset.assetType; }
function sentenceCase(value: string) { return value ? value.charAt(0).toUpperCase() + value.slice(1) : value; }
function findingDate(value?: number) { return value ? new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value)) : "Not recorded"; }
async function copyText(value: string) { if (navigator.clipboard?.writeText) { try { await navigator.clipboard.writeText(value); return; } catch { /* Fall through for browsers that deny the Clipboard API. */ } } const input = document.createElement("textarea"); input.value = value; input.style.position = "fixed"; input.style.opacity = "0"; document.body.appendChild(input); input.select(); document.execCommand("copy"); input.remove(); }
function exportAssets(assets: ExposureAsset[]) { const rows = [["host","asset_type","ips","open_ports","open_risks","highest_risk_severity","last_seen"], ...assets.map((asset) => [asset.host,asset.assetType ?? "",asset.ips.join(" "),asset.ports.join(" "),String(asset.openRiskCount),asset.highestRiskSeverity ?? "",String(asset.lastSeenAt ?? "")])]; const csv = rows.map((row) => row.map((cell) => `"${cell.replaceAll('"','""')}"`).join(",")).join("\n"); const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); const link = document.createElement("a"); link.href = url; link.download = "vaayu-demo-assets.csv"; link.click(); URL.revokeObjectURL(url); }
