import {
  Braces,
  ExternalLink,
  FileText,
  Gauge,
  Globe,
  MessageSquareText,
  Send,
  Server,
  ShieldAlert,
} from "lucide-react";
import { useState } from "react";
import { useRiskComments } from "../hooks/useRiskComments";
import { useRiskEvidence } from "../hooks/useRiskEvidence";
import { InlineCodeText } from "./InlineCodeText";
import { RiskEvidencePanel } from "./RiskEvidencePanel";
import type { LiveFinding, RiskComment, RiskEvidenceResponse, RiskOverview } from "../types";

type OverviewState = "idle" | "loading" | "ready" | "unavailable";
type DetailTab = "overview" | "evidence" | "notes";
export function RiskDetailWorkspace({
  finding,
  overview,
  overviewState,
  severity,
  status,
  findingId,
  token,
  onAuthExpired,
}: {
  finding: LiveFinding;
  overview: RiskOverview | null;
  overviewState: OverviewState;
  severity: string;
  status: string;
  findingId: string | null;
  token: string | null;
  onAuthExpired: () => void;
}) {
  const [tab, setTab] = useState<DetailTab>("overview");
  const [draft, setDraft] = useState("");
  const { result: evidence, state: evidenceState } = useRiskEvidence(findingId, token, onAuthExpired);
  const { comments, state: commentsState, addComment } = useRiskComments(findingId, token, onAuthExpired);
  const tabs: { id: DetailTab; label: string; icon: React.ReactNode; count?: number }[] = [
    { id: "overview", label: "Overview", icon: <FileText size={15} /> },
    { id: "evidence", label: "Evidence", icon: <Braces size={15} /> },
    { id: "notes", label: "Notes", icon: <MessageSquareText size={15} />, count: comments.length },
  ];
  const addNote = async () => {
    const body = draft.trim();
    if (body && await addComment(body)) setDraft("");
  };

  return <>
    <nav aria-label="Risk detail sections" className="risks-detail-tabs" role="tablist">
      {tabs.map((item) => <button aria-controls={`risk-panel-${item.id}`} aria-selected={tab === item.id} className="risks-detail-tab" id={`risk-tab-${item.id}`} key={item.id} onClick={() => setTab(item.id)} role="tab" type="button">
        {item.icon}<span>{item.label}</span>{item.count !== undefined ? <b>{item.count}</b> : null}
      </button>)}
    </nav>
    <div className="drawer-body assets-drawer-body risks-drawer-body">
      <section aria-labelledby={`risk-tab-${tab}`} id={`risk-panel-${tab}`} role="tabpanel">
        {tab === "overview" ? <OverviewPanel evidence={evidence} finding={finding} overview={overview} overviewState={overviewState} severity={severity} status={status} /> : null}
        {tab === "evidence" ? <RiskEvidencePanel finding={finding} result={evidence} state={evidenceState} /> : null}
        {tab === "notes" ? <NotesPanel comments={comments} draft={draft} onAdd={() => void addNote()} onDraft={setDraft} state={commentsState} /> : null}
      </section>
    </div>
  </>;
}

function OverviewPanel({ evidence, finding, overview, overviewState, severity, status }: { evidence: RiskEvidenceResponse | null; finding: LiveFinding; overview: RiskOverview | null; overviewState: OverviewState; severity: string; status: string }) {
  const active = status !== "remediated";
  const technologies = finding.technologies?.filter(Boolean) ?? [];
  const signals = finding.surface_hints?.filter(Boolean) ?? [];
  return <div className="risks-tab-panel">
    <section className={`assets-overview-hero risks-overview-hero risks-overview-hero-${active ? severity : "clear"}`}>
      <span className="assets-overview-hero-icon risks-overview-hero-icon">{active ? <ShieldAlert size={20} /> : <Globe size={20} />}</span>
      <div className="assets-overview-hero-copy"><span className="assets-card-kicker">Risk posture</span><h3>{active ? `${label(severity)} risk requires review` : "This risk is marked remediated"}</h3><p>{findingTitle(finding)} was observed on <span className="mono">{finding.host}{finding.port ? `:${finding.port}` : ""}</span>.</p></div>
      <div className="assets-overview-hero-meta"><span className={`badge risks-severity risks-severity-${severity}`}>{label(severity)}</span><small>Priority {Math.max(0, finding.priority)}/5</small></div>
    </section>
    <RiskOverviewSections overview={overview} state={overviewState} />
    <ImpactedAssets finding={finding} result={evidence} />
    <div className="assets-overview-grid">
      <DetailCard title="Detection context" icon={<Gauge size={15} />}><Detail label="Assessment source" value={sourceLabel(finding.source)} /><Detail label="HTTP response" value={finding.status ? `${finding.status} · ${httpStatusLabel(finding.status)}` : "Not recorded"} /><Detail label="Server" value={finding.server || "Not identified"} /><Detail label="Response time" value={finding.latency_ms !== undefined ? `${finding.latency_ms} ms` : "Not recorded"} mono /><Detail label="Last checked" value={dateLabel(finding.last_seen_at)} /></DetailCard>
      <section className="assets-detail-card"><CardHeading kicker="Observed stack" title="Technologies" aside={`${technologies.length} identified`} /><div className="assets-tech-grid">{technologies.length ? technologies.map((value) => <span className="assets-tech-item" key={value}><Server size={14} /><span>{value}</span></span>) : <span className="dim">No technology was recorded.</span>}</div><div className="assets-pills risks-signal-pills risks-overview-signals">{signals.map((value) => <span className="badge badge-outline" key={value}>{humanize(value)}</span>)}</div></section>
    </div>
  </div>;
}

function NotesPanel({ comments, draft, onAdd, onDraft, state }: { comments: RiskComment[]; draft: string; onAdd: () => void; onDraft: (value: string) => void; state: OverviewState }) {
  return <div className="risks-tab-panel">
    <section className="assets-detail-card risks-note-composer"><CardHeading kicker="Demo timeline" title="Add a comment" aside="Users and agents" /><p className="risks-note-help">Demo notes stay in this tab. They are not sent to us or saved.</p><textarea aria-label="Add a comment" maxLength={4000} onChange={(event) => onDraft(event.target.value)} placeholder="Add context, a decision, an owner, or a remediation update…" value={draft} /><div><span>{draft.length}/4,000 · Demo only, this tab</span><button className="btn btn-primary" disabled={!draft.trim()} onClick={onAdd} type="button"><Send size={13} /> Comment</button></div></section>
    <section className="assets-detail-card"><CardHeading kicker="Activity" title="Comments" aside={`${comments.length} ${comments.length === 1 ? "comment" : "comments"}`} />{state === "loading" ? <span className="dim">Loading comments…</span> : null}{state === "unavailable" ? <span className="dim">Comments are temporarily unavailable.</span> : null}<div className="risks-note-list">{comments.map((comment) => <article key={comment.comment_id}><span className={`risks-note-avatar risks-note-avatar-${comment.actor_type}`}>{initials(comment.display_name)}</span><div><header><strong>{comment.display_name}</strong><span className={`risks-actor-badge risks-actor-badge-${comment.actor_type}`}>{comment.actor_type}</span><time>{dateTimeLabel(comment.created_at)}</time></header><p>{comment.body}</p></div></article>)}</div></section>
  </div>;
}

function RiskOverviewSections({ overview, state }: { overview: RiskOverview | null; state: OverviewState }) {
  if (state === "loading") return <section className="assets-detail-card risks-overview-document"><span className="dim">Loading vulnerability overview…</span></section>;
  if (state === "unavailable") return <section className="assets-detail-card risks-overview-document"><span className="dim">The vulnerability overview is temporarily unavailable.</span></section>;
  if (!overview || overview.status !== "ready") {
    const message = overview?.status === "review_required" ? "The researched overview requires editorial review." : overview?.status === "failed" ? "The researched overview could not be generated." : "A researched overview is being prepared for this risk.";
    return <section className="assets-detail-card risks-overview-document"><span className="dim">{message}</span></section>;
  }
  return <section className="assets-detail-card risks-overview-document" aria-label="Vulnerability overview"><div><h3>Vulnerability description</h3><p><InlineCodeText text={overview.vulnerability_description} /></p></div><div><h3>Impact</h3><p><InlineCodeText text={overview.impact} /></p></div><div><h3>Remediation</h3><p><InlineCodeText text={overview.remediation?.summary} /></p><ul>{overview.remediation?.steps.map((step) => <li key={step}><InlineCodeText text={step} /></li>)}</ul></div><div><h3>References</h3><ul className="risks-overview-references">{overview.references?.map((reference) => <li key={reference.url}><a href={reference.url} rel="noreferrer" target="_blank">{reference.title}<ExternalLink size={11} /></a><span>{reference.publisher}</span></li>)}</ul></div></section>;
}

function ImpactedAssets({ finding, result }: { finding: LiveFinding; result: RiskEvidenceResponse | null }) { const impacts = result?.impacts ?? []; return <section className="assets-detail-card"><CardHeading kicker="Impact scope" title="Impacted assets" aside={`${impacts.length || 1} ${impacts.length === 1 ? "location" : "locations"}`} /><div className="risks-impacted-table"><div className="risks-impacted-head"><span>Asset</span><span>Location</span><span>Evidence source</span><span>Last seen</span></div>{impacts.length ? impacts.map((impact) => <div key={impact.impact_id}><strong className="mono">{impact.asset}</strong><span className="mono">{impact.locator}</span><span>{evidenceSourceLabel(finding)}</span><time>{dateTimeLabel(impact.last_seen_at)}</time></div>) : <div><strong className="mono">{finding.host}</strong><span className="mono">{finding.url || (finding.port ? `${finding.host}:${finding.port}` : finding.host)}</span><span>{evidenceSourceLabel(finding)}</span><time>{dateLabel(finding.last_seen_at)}</time></div>}</div></section>; }
function evidenceSourceLabel(finding: LiveFinding) { return finding.source === "vulnerability-scanning" ? "Vaayu active testing" : finding.source === "stage1" ? "Vaayu secret discovery" : "Vaayu security capability"; }
function DetailCard({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) { return <section className="assets-detail-card"><div className="assets-card-heading"><h3 className="risks-card-title">{icon}{title}</h3></div><div className="risks-detail-list">{children}</div></section>; }
function Detail({ label: name, value, mono = false }: { label: string; value: React.ReactNode; mono?: boolean }) { return <div><span>{name}</span><strong className={mono ? "mono" : undefined}>{value}</strong></div>; }
function CardHeading({ kicker, title, aside }: { kicker: string; title: string; aside: string }) { return <div className="assets-card-heading"><div><span className="assets-card-kicker">{kicker}</span><h3>{title}</h3></div><span>{aside}</span></div>; }
function findingTitle(finding: LiveFinding) { return finding.title?.trim() || finding.template_id?.trim() || "Finding metadata pending"; }
function sourceLabel(value?: LiveFinding["source"]) { return value === "stage1" ? "Vaayu reconnaissance" : value === "vulnerability-scanning" ? "Vaayu active testing" : "Vaayu security assessment"; }
function humanize(value: string) { return value.replaceAll(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function httpStatusLabel(status: number) { if (status >= 200 && status < 300) return "Successful response"; if (status >= 300 && status < 400) return "Redirect"; if (status === 401) return "Sign-in required"; if (status === 403) return "Forbidden"; if (status === 404) return "Not found"; if (status >= 500) return "Server error"; return status >= 400 ? "Request failed" : "Observed"; }
function dateLabel(value?: number) { if (!value) return "Not recorded"; return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value)); }
function dateTimeLabel(value: string) { if (!value) return "Not recorded"; return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value)); }
function label(value: string) { return value.charAt(0).toUpperCase() + value.slice(1); }
function initials(name: string) { return name.split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase(); }
