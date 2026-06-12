import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import {
  ArrowRight,
  BriefcaseBusiness,
  ChevronRight,
  CircleDollarSign,
  Home,
  Info,
  Layers,
  MapPin,
  RefreshCw,
  Search,
  Sparkles,
  Tag,
  UserRound,
  Users,
  X,
} from 'lucide-react';

interface AdminUser {
  id: string;
  name: string | null;
  city: string | null;
}

interface GraphNode {
  id: string;
  label: string;
  type: string;
  properties: Record<string, any>;
}

interface GraphLink {
  source: string;
  target: string;
  type: string;
  properties: Record<string, any>;
}

interface GraphData {
  user_id: string;
  nodes: GraphNode[];
  links: GraphLink[];
}

interface LeadItem {
  user_id: string;
  lead_id: string;
  category: string;
  product: string;
  attrs: any;
  budget_signal: string | null;
  intent_score: number | null;
  confidence: number;
  last_seen_at: number;
  created_at: number;
  consent_status: string;
  status?: string;
}

interface AdminDashboardProps {
  onClose: () => void;
}

const TYPE_STYLES: Record<string, { label: string; dot: string; ring: string; bg: string }> = {
  User: { label: 'Profile owner', dot: 'bg-slate-950', ring: 'ring-slate-300', bg: 'bg-slate-950 text-white' },
  Place: { label: 'Saved location', dot: 'bg-emerald-500', ring: 'ring-emerald-200', bg: 'bg-emerald-50 text-emerald-800' },
  Category: { label: 'Interest category', dot: 'bg-violet-500', ring: 'ring-violet-200', bg: 'bg-violet-50 text-violet-800' },
  Interest: { label: 'Specific interest', dot: 'bg-sky-500', ring: 'ring-sky-200', bg: 'bg-sky-50 text-sky-800' },
  Lead: { label: 'Commercial intent', dot: 'bg-rose-500', ring: 'ring-rose-200', bg: 'bg-rose-50 text-rose-800' },
};

const PROFILE_FIELDS = ['name', 'age', 'city', 'language', 'email', 'phone'];

function formatRelationship(type: string) {
  return type.toLowerCase().replace(/_/g, ' ');
}

function formatValue(value: any) {
  if (value === null || value === undefined || value === '') return 'Not captured';
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}

function compactDate(value?: number | null) {
  if (!value) return 'N/A';
  return new Date(value).toLocaleDateString();
}

function getNodeStyle(type: string) {
  return TYPE_STYLES[type] || {
    label: type || 'Graph node',
    dot: 'bg-slate-400',
    ring: 'ring-slate-200',
    bg: 'bg-slate-100 text-slate-700',
  };
}

function relationBetween(links: GraphLink[], source: string, target: string) {
  return links.find((link) => link.source === source && link.target === target);
}

function TabPills({
  activeTab,
  onChange,
}: {
  activeTab: 'graph' | 'leads';
  onChange: (tab: 'graph' | 'leads') => void;
}) {
  return (
    <>
      {(['graph', 'leads'] as const).map((tab) => (
        <button
          key={tab}
          onClick={() => onChange(tab)}
          className={`rounded-full px-4 py-1.5 text-xs font-semibold capitalize transition-colors ${
            activeTab === tab ? 'bg-slate-950 text-white' : 'text-slate-500 hover:text-slate-950'
          }`}
        >
          {tab}
        </button>
      ))}
    </>
  );
}

function NodeInspector({
  node,
  style,
}: {
  node: GraphNode;
  style: { label: string; dot: string; ring: string; bg: string };
}) {
  return (
    <div className="space-y-4">
      <div>
        <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${style.bg}`}>
          {style.label}
        </span>
        <h3 className="mt-3 break-words text-lg font-bold text-slate-950">{node.label}</h3>
        <p className="mt-1 break-all text-xs text-slate-500">{node.id}</p>
      </div>
      <div className="space-y-2">
        {Object.entries(node.properties || {}).length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-300 p-3 text-xs text-slate-500">No metadata fields.</div>
        ) : (
          Object.entries(node.properties).map(([key, value]) => (
            <div key={key} className="rounded-lg bg-slate-50 p-3">
              <div className="text-[11px] font-bold uppercase tracking-wide text-slate-400">{key}</div>
              <pre className="mt-1 whitespace-pre-wrap break-words font-sans text-xs text-slate-800">{formatValue(value)}</pre>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function NodeCard({
  node,
  selected,
  relation,
  onSelect,
  children,
}: {
  node: GraphNode;
  selected: boolean;
  relation?: string;
  onSelect: (node: GraphNode) => void;
  children?: React.ReactNode;
}) {
  const style = getNodeStyle(node.type);
  return (
    <button
      onClick={() => onSelect(node)}
      className={`w-full text-left rounded-lg border bg-white/80 p-3 shadow-sm shadow-slate-900/5 transition-all hover:-translate-y-0.5 hover:border-slate-300 hover:bg-white ${
        selected ? 'border-slate-950 ring-2 ring-slate-950/10' : 'border-slate-200/80'
      }`}
    >
      <div className="flex items-start gap-3">
        <span className={`mt-1 h-3 w-3 rounded-full ${style.dot} ring-4 ${style.ring} shrink-0`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold text-slate-950">{node.label}</span>
            <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${style.bg}`}>
              {node.type}
            </span>
          </div>
          {relation && (
            <div className="mt-1 flex items-center gap-1 text-[11px] font-medium text-slate-500">
              <ArrowRight size={11} />
              {formatRelationship(relation)}
            </div>
          )}
          {children}
        </div>
      </div>
    </button>
  );
}

export function AdminDashboard({ onClose }: AdminDashboardProps) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [selectedUserId, setSelectedUserId] = useState<string>('');
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loadingGraph, setLoadingGraph] = useState(false);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [activeTab, setActiveTab] = useState<'graph' | 'leads'>('graph');
  const [leads, setLeads] = useState<LeadItem[]>([]);
  const [loadingLeads, setLoadingLeads] = useState(false);
  const [leadCategoryFilter, setLeadCategoryFilter] = useState('');
  const [leadStatusFilter, setLeadStatusFilter] = useState('open');

  useEffect(() => {
    const fetchUsers = async () => {
      try {
        const list = await api.get('/api/admin/users/');
        setUsers(list);
        if (list.length > 0) setSelectedUserId(list[0].id);
      } catch (err) {
        console.error('Failed to load admin users', err);
      }
    };
    fetchUsers();
  }, []);

  useEffect(() => {
    if (!selectedUserId) return;
    const fetchGraph = async () => {
      setLoadingGraph(true);
      setSelectedNode(null);
      try {
        const data = await api.get(`/api/admin/profile-graph/${selectedUserId}/`);
        setGraphData({
          user_id: data.user_id,
          nodes: data.nodes || [],
          links: data.links || [],
        });
      } catch (err) {
        console.error('Failed to load user graph', err);
      } finally {
        setLoadingGraph(false);
      }
    };
    fetchGraph();
  }, [selectedUserId]);

  const fetchLeads = async () => {
    setLoadingLeads(true);
    try {
      const params = new URLSearchParams();
      if (leadCategoryFilter) params.append('category', leadCategoryFilter);
      if (leadStatusFilter) params.append('status', leadStatusFilter);
      const list = await api.get(`/api/leads/?${params.toString()}`);
      setLeads(list);
    } catch (err) {
      console.error('Failed to load leads', err);
    } finally {
      setLoadingLeads(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'leads') fetchLeads();
  }, [activeTab, leadCategoryFilter, leadStatusFilter]);

  const userNode = useMemo(
    () => graphData?.nodes.find((node) => node.type === 'User') || null,
    [graphData],
  );

  const hierarchy = useMemo(() => {
    const nodes = graphData?.nodes || [];
    const links = graphData?.links || [];
    const categories = nodes.filter((node) => node.type === 'Category');
    const interests = nodes.filter((node) => node.type === 'Interest');
    const leads = nodes.filter((node) => node.type === 'Lead');

    return {
      places: nodes.filter((node) => node.type === 'Place'),
      categories: categories.map((category) => {
        const categoryInterests = interests.filter((interest) => {
          const explicit = relationBetween(links, category.id, interest.id);
          const sameCategory = interest.properties?.category === category.id || interest.properties?.category === category.label;
          return Boolean(explicit) || sameCategory;
        });
        const categoryLeads = leads.filter((lead) => {
          const explicit = relationBetween(links, lead.id, category.id);
          const sameCategory = String(lead.properties?.category || '').toLowerCase() === String(category.label).toLowerCase();
          return Boolean(explicit) || sameCategory;
        });
        return { category, interests: categoryInterests, leads: categoryLeads };
      }),
      uncategorizedInterests: interests.filter((interest) => !categories.some((category) => {
        const explicit = relationBetween(links, category.id, interest.id);
        const sameCategory = interest.properties?.category === category.id || interest.properties?.category === category.label;
        return Boolean(explicit) || sameCategory;
      })),
      uncategorizedLeads: leads.filter((lead) => !categories.some((category) => {
        const explicit = relationBetween(links, lead.id, category.id);
        const sameCategory = String(lead.properties?.category || '').toLowerCase() === String(category.label).toLowerCase();
        return Boolean(explicit) || sameCategory;
      })),
    };
  }, [graphData]);

  const selectedUser = users.find((u) => u.id === selectedUserId);
  const selectedStyle = selectedNode ? getNodeStyle(selectedNode.type) : null;
  const graphCounts = {
    places: hierarchy.places.length,
    categories: hierarchy.categories.length,
    interests: hierarchy.categories.reduce((sum, entry) => sum + entry.interests.length, 0) + hierarchy.uncategorizedInterests.length,
    leads: hierarchy.categories.reduce((sum, entry) => sum + entry.leads.length, 0) + hierarchy.uncategorizedLeads.length,
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-transparent text-slate-900">
      <header className="h-16 shrink-0 border-b border-slate-200/80 bg-white/75 px-4 backdrop-blur-md md:px-6">
        <div className="mx-auto flex h-full max-w-[1500px] items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-slate-950 text-white shadow-sm">
              <Layers size={18} />
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-bold tracking-tight text-slate-950">Knowledge Graph Admin</h1>
              <p className="truncate text-xs text-slate-500">Profile hierarchy, interests, locations, and commercial intent</p>
            </div>
          </div>

          <div className="hidden rounded-full border border-slate-200 bg-white/80 p-1 shadow-sm md:flex">
            <TabPills activeTab={activeTab} onChange={setActiveTab} />
          </div>

          <button
            onClick={onClose}
            className="flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white/80 text-slate-500 shadow-sm transition-colors hover:text-slate-950"
            title="Close admin dashboard"
            aria-label="Close admin dashboard"
          >
            <X size={16} />
          </button>
        </div>
      </header>

      <div className="flex shrink-0 items-center gap-3 border-b border-slate-200/80 bg-white/60 px-4 py-2 md:hidden">
        <div className="flex shrink-0 rounded-full border border-slate-200 bg-white/80 p-1 shadow-sm">
          <TabPills activeTab={activeTab} onChange={setActiveTab} />
        </div>
        <select
          value={selectedUserId}
          onChange={(e) => setSelectedUserId(e.target.value)}
          aria-label="Select user"
          className="h-9 min-w-0 flex-1 rounded-full border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-400"
        >
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name || u.id}
            </option>
          ))}
        </select>
      </div>

      <div className="mx-auto flex min-h-0 w-full max-w-[1500px] flex-1 overflow-hidden">
        <aside className="hidden w-72 shrink-0 border-r border-slate-200/80 bg-white/45 md:flex md:flex-col">
          <div className="border-b border-slate-200/80 p-4">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2 text-xs font-bold text-slate-700">
                <Users size={14} />
                Users
              </span>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
                {users.length}
              </span>
            </div>
          </div>
          <div className="flex-1 space-y-1 overflow-y-auto p-2">
            {users.map((u) => (
              <button
                key={u.id}
                onClick={() => setSelectedUserId(u.id)}
                className={`w-full rounded-lg border p-3 text-left transition-colors ${
                  selectedUserId === u.id
                    ? 'border-slate-300 bg-white shadow-sm'
                    : 'border-transparent text-slate-600 hover:bg-white/70'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-semibold text-slate-950">{u.name || u.id}</span>
                  <ChevronRight size={14} className="shrink-0 text-slate-400" />
                </div>
                <div className="mt-1 flex items-center gap-1 text-xs text-slate-500">
                  <MapPin size={11} />
                  <span className="truncate">{u.city || 'City unknown'}</span>
                </div>
              </button>
            ))}
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto p-4 md:p-6">
          {activeTab === 'graph' ? (
            <div className="space-y-5">
              <section className="rounded-xl border border-slate-200 bg-white/70 p-4 shadow-sm shadow-slate-900/5">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                  <div className="flex items-center gap-3">
                    <div className="flex h-11 w-11 items-center justify-center rounded-full bg-slate-950 text-white">
                      <UserRound size={18} />
                    </div>
                    <div>
                      <h2 className="text-lg font-bold text-slate-950">{selectedUser?.name || userNode?.label || selectedUserId}</h2>
                      <p className="text-xs text-slate-500">{selectedUserId}</p>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                    <span className="rounded-lg bg-slate-100 px-3 py-2 font-semibold text-slate-600">{graphCounts.places} places</span>
                    <span className="rounded-lg bg-slate-100 px-3 py-2 font-semibold text-slate-600">{graphCounts.categories} categories</span>
                    <span className="rounded-lg bg-slate-100 px-3 py-2 font-semibold text-slate-600">{graphCounts.interests} interests</span>
                    <span className="rounded-lg bg-slate-100 px-3 py-2 font-semibold text-slate-600">{graphCounts.leads} leads</span>
                  </div>
                </div>
              </section>

              {loadingGraph ? (
                <div className="flex h-80 items-center justify-center rounded-xl border border-slate-200 bg-white/60">
                  <div className="flex items-center gap-2 text-sm font-medium text-slate-500">
                    <RefreshCw size={16} className="animate-spin" />
                    Loading graph
                  </div>
                </div>
              ) : !graphData || graphData.nodes.length === 0 ? (
                <div className="flex h-80 items-center justify-center rounded-xl border border-slate-200 bg-white/60 text-sm text-slate-500">
                  No graph nodes found for this profile.
                </div>
              ) : (
                <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
                  <section className="grid gap-4 lg:grid-cols-3">
                    <div className="space-y-3">
                      <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-slate-500">
                        <UserRound size={14} />
                        Profile
                      </div>
                      {userNode && (
                        <NodeCard node={userNode} selected={selectedNode?.id === userNode.id} onSelect={setSelectedNode}>
                          <div className="mt-3 grid gap-2">
                            {PROFILE_FIELDS.map((field) => (
                              <div key={field} className="flex items-center justify-between gap-3 rounded-md bg-slate-50 px-2.5 py-2 text-xs">
                                <span className="font-medium capitalize text-slate-500">{field}</span>
                                <span className="min-w-0 truncate font-semibold text-slate-800">{formatValue(userNode.properties?.[field])}</span>
                              </div>
                            ))}
                          </div>
                        </NodeCard>
                      )}

                      <div className="flex items-center gap-2 pt-2 text-xs font-bold uppercase tracking-wide text-slate-500">
                        <MapPin size={14} />
                        Places
                      </div>
                      {hierarchy.places.length === 0 ? (
                        <div className="rounded-lg border border-dashed border-slate-300 bg-white/50 p-3 text-xs text-slate-500">No saved locations yet.</div>
                      ) : (
                        hierarchy.places.map((place) => {
                          const relation = graphData.links.find((link) => link.target === place.id);
                          return (
                            <NodeCard
                              key={place.id}
                              node={place}
                              selected={selectedNode?.id === place.id}
                              relation={relation?.type}
                              onSelect={setSelectedNode}
                            >
                              <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
                                {relation?.type === 'LIVES_AT' ? <Home size={12} /> : <BriefcaseBusiness size={12} />}
                                {place.properties?.kind || 'location'}
                              </div>
                            </NodeCard>
                          );
                        })
                      )}
                    </div>

                    <div className="space-y-3 lg:col-span-2">
                      <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-slate-500">
                        <Tag size={14} />
                        Categories, Interests, Leads
                      </div>
                      {hierarchy.categories.length === 0 && hierarchy.uncategorizedInterests.length === 0 && hierarchy.uncategorizedLeads.length === 0 ? (
                        <div className="rounded-lg border border-dashed border-slate-300 bg-white/50 p-4 text-sm text-slate-500">
                          No interests or commercial intents have been captured for this user.
                        </div>
                      ) : (
                        <div className="space-y-3">
                          {hierarchy.categories.map(({ category, interests, leads: categoryLeads }) => (
                            <div key={category.id} className="rounded-xl border border-slate-200 bg-white/70 p-3 shadow-sm shadow-slate-900/5">
                              <NodeCard
                                node={category}
                                selected={selectedNode?.id === category.id}
                                relation={relationBetween(graphData.links, graphData.user_id, category.id)?.type}
                                onSelect={setSelectedNode}
                              />
                              <div className="mt-3 grid gap-3 md:grid-cols-2">
                                <div className="space-y-2">
                                  <div className="text-[11px] font-bold uppercase tracking-wide text-slate-400">Specific interests</div>
                                  {interests.length === 0 ? (
                                    <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500">No leaf interests.</div>
                                  ) : (
                                    interests.map((interest) => (
                                      <NodeCard
                                        key={interest.id}
                                        node={interest}
                                        selected={selectedNode?.id === interest.id}
                                        relation={relationBetween(graphData.links, category.id, interest.id)?.type || relationBetween(graphData.links, graphData.user_id, interest.id)?.type}
                                        onSelect={setSelectedNode}
                                      >
                                        <div className="mt-2 text-xs text-slate-500">
                                          Weight {formatValue(relationBetween(graphData.links, graphData.user_id, interest.id)?.properties?.weight ?? interest.properties?.weight)}
                                        </div>
                                      </NodeCard>
                                    ))
                                  )}
                                </div>
                                <div className="space-y-2">
                                  <div className="text-[11px] font-bold uppercase tracking-wide text-slate-400">Commercial intent</div>
                                  {categoryLeads.length === 0 ? (
                                    <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500">No active leads.</div>
                                  ) : (
                                    categoryLeads.map((lead) => (
                                      <NodeCard
                                        key={lead.id}
                                        node={lead}
                                        selected={selectedNode?.id === lead.id}
                                        relation={relationBetween(graphData.links, graphData.user_id, lead.id)?.type}
                                        onSelect={setSelectedNode}
                                      >
                                        <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
                                          <span className="rounded-full bg-rose-50 px-2 py-1 font-semibold text-rose-700">
                                            Intent {formatValue(lead.properties?.intent_score)}
                                          </span>
                                          <span className="rounded-full bg-amber-50 px-2 py-1 font-semibold text-amber-700">
                                            {lead.properties?.budget_signal || 'budget unknown'}
                                          </span>
                                        </div>
                                      </NodeCard>
                                    ))
                                  )}
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </section>

                  <aside className="hidden rounded-xl border border-slate-200 bg-white/75 p-4 shadow-sm shadow-slate-900/5 xl:block">
                    <div className="mb-4 flex items-center gap-2">
                      <Info size={15} className="text-slate-500" />
                      <span className="text-sm font-bold text-slate-950">Selected Node</span>
                    </div>
                    {selectedNode && selectedStyle ? (
                      <NodeInspector node={selectedNode} style={selectedStyle} />
                    ) : (
                      <div className="flex min-h-52 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-slate-300 bg-white/50 p-6 text-center text-sm text-slate-500">
                        <Sparkles size={18} />
                        Select a node to inspect its metadata inline.
                      </div>
                    )}
                  </aside>
                </div>
              )}

              {selectedNode && selectedStyle && (
                <div className="fixed inset-0 z-[200] xl:hidden">
                  <div
                    className="absolute inset-0 bg-slate-950/40 backdrop-blur-sm"
                    onClick={() => setSelectedNode(null)}
                  />
                  <div className="absolute inset-x-0 bottom-0 max-h-[75vh] overflow-y-auto rounded-t-3xl border-t border-slate-200 bg-white p-5 pb-[calc(1.25rem+env(safe-area-inset-bottom))] shadow-[0_-20px_60px_-15px_rgba(15,23,42,0.25)]">
                    <div className="mb-4 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Info size={15} className="text-slate-500" />
                        <span className="text-sm font-bold text-slate-950">Selected Node</span>
                      </div>
                      <button
                        onClick={() => setSelectedNode(null)}
                        aria-label="Close node details"
                        className="flex h-8 w-8 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition-colors hover:text-slate-950"
                      >
                        <X size={14} />
                      </button>
                    </div>
                    <NodeInspector node={selectedNode} style={selectedStyle} />
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              <section className="rounded-xl border border-slate-200 bg-white/70 p-4 shadow-sm shadow-slate-900/5">
                <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
                  <div>
                    <h2 className="text-lg font-bold text-slate-950">Commercial Leads</h2>
                    <p className="text-sm text-slate-500">Open purchase intent extracted from conversations.</p>
                  </div>
                  <div className="flex flex-wrap items-end gap-3">
                    <label className="flex flex-col gap-1 text-xs font-semibold text-slate-500">
                      Category
                      <span className="relative">
                        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input
                          type="text"
                          placeholder="food, travel..."
                          value={leadCategoryFilter}
                          onChange={(e) => setLeadCategoryFilter(e.target.value)}
                          className="h-9 w-44 rounded-full border border-slate-200 bg-white pl-8 pr-3 text-sm text-slate-900 outline-none focus:border-slate-400"
                        />
                      </span>
                    </label>
                    <label className="flex flex-col gap-1 text-xs font-semibold text-slate-500">
                      Status
                      <select
                        value={leadStatusFilter}
                        onChange={(e) => setLeadStatusFilter(e.target.value)}
                        className="h-9 rounded-full border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-400"
                      >
                        <option value="open">Open</option>
                        <option value="contacted">Contacted</option>
                        <option value="converted">Converted</option>
                        <option value="archived">Archived</option>
                      </select>
                    </label>
                    <button
                      onClick={fetchLeads}
                      className="flex h-9 items-center gap-2 rounded-full bg-slate-950 px-4 text-sm font-semibold text-white transition-colors hover:bg-slate-800"
                    >
                      <RefreshCw size={14} className={loadingLeads ? 'animate-spin' : ''} />
                      Refresh
                    </button>
                  </div>
                </div>
              </section>

              <section className="overflow-hidden rounded-xl border border-slate-200 bg-white/75 shadow-sm shadow-slate-900/5">
                {loadingLeads ? (
                  <div className="flex h-72 items-center justify-center">
                    <RefreshCw size={18} className="animate-spin text-slate-500" />
                  </div>
                ) : leads.length === 0 ? (
                  <div className="flex h-72 items-center justify-center text-sm text-slate-500">No matching leads found.</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[820px] text-left text-sm">
                      <thead className="border-b border-slate-200 bg-slate-50 text-xs font-bold uppercase tracking-wide text-slate-500">
                        <tr>
                          <th className="p-4">Product or service</th>
                          <th className="p-4">Category</th>
                          <th className="p-4">Budget</th>
                          <th className="p-4">Intent</th>
                          <th className="p-4">Consent</th>
                          <th className="p-4">Last seen</th>
                          <th className="p-4">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-200/80">
                        {leads.map((lead) => (
                          <tr key={lead.lead_id} className="hover:bg-slate-50/70">
                            <td className="p-4">
                              <div className="font-semibold text-slate-950">{lead.product}</div>
                              <div className="mt-1 text-xs text-slate-500">{lead.lead_id.slice(0, 16)}...</div>
                            </td>
                            <td className="p-4">
                              <span className="rounded-full bg-violet-50 px-2.5 py-1 text-xs font-semibold text-violet-700">{lead.category}</span>
                            </td>
                            <td className="p-4 text-sm text-slate-600">{lead.budget_signal || 'unknown'}</td>
                            <td className="p-4">
                              <div className="flex items-center gap-2">
                                <span className="font-semibold text-slate-800">{lead.intent_score !== null ? lead.intent_score.toFixed(2) : 'N/A'}</span>
                                <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
                                  <div className="h-full rounded-full bg-slate-950" style={{ width: `${(lead.intent_score || 0) * 100}%` }} />
                                </div>
                              </div>
                            </td>
                            <td className="p-4 text-sm capitalize text-slate-600">{lead.consent_status}</td>
                            <td className="p-4 text-sm text-slate-600">{compactDate(lead.last_seen_at)}</td>
                            <td className="p-4">
                              <button
                                onClick={() => {
                                  setActiveTab('graph');
                                  setSelectedUserId(lead.user_id);
                                }}
                                className="inline-flex items-center gap-1 rounded-full border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-slate-300 hover:bg-white"
                              >
                                <CircleDollarSign size={13} />
                                View profile
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
