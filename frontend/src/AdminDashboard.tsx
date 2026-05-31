import { useState, useEffect, useRef } from 'react'
import { api } from './api'
import { 
  Users, 
  Layers, 
  MapPin, 
  Sparkles, 
  ChevronRight, 
  RefreshCw, 
  Search, 
  Info
} from 'lucide-react'

interface AdminUser {
  id: string;
  name: string | null;
  city: string | null;
}

interface GraphNode {
  id: string;
  label: string;
  type: string;
  properties: any;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
}

interface GraphLink {
  source: string;
  target: string;
  type: string;
  properties: any;
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

const TYPE_COLORS: Record<string, string> = {
  User: '#F59E0B',       // Amber
  Place: '#10B981',      // Emerald
  Category: '#8B5CF6',   // Violet
  Interest: '#0EA5E9',   // Sky Blue
  Lead: '#F43F5E',       // Rose
  Default: '#64748B'     // Slate
};

const TYPE_LABELS: Record<string, string> = {
  User: 'Profile Owner',
  Place: 'Saved Location',
  Category: 'Interest Category',
  Interest: 'Specific Interest',
  Lead: 'Purchase Intent Lead'
};

export function AdminDashboard({ onClose }: AdminDashboardProps) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [selectedUserId, setSelectedUserId] = useState<string>('');
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loadingGraph, setLoadingGraph] = useState<boolean>(false);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  
  // Leads tab state
  const [activeTab, setActiveTab] = useState<'graph' | 'leads'>('graph');
  const [leads, setLeads] = useState<LeadItem[]>([]);
  const [loadingLeads, setLoadingLeads] = useState<boolean>(false);
  const [leadCategoryFilter, setLeadCategoryFilter] = useState<string>('');
  const [leadStatusFilter, setLeadStatusFilter] = useState<string>('open');

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animationRef = useRef<number | null>(null);
  const nodesRef = useRef<GraphNode[]>([]);
  const linksRef = useRef<GraphLink[]>([]);
  const draggingNodeRef = useRef<GraphNode | null>(null);
  const hoveredNodeRef = useRef<GraphNode | null>(null);

  // Load user directory
  useEffect(() => {
    const fetchUsers = async () => {
      try {
        const list = await api.get('/api/admin/users/');
        setUsers(list);
        if (list.length > 0) {
          setSelectedUserId(list[0].id);
        }
      } catch (err) {
        console.error('Failed to load admin users', err);
      }
    };
    fetchUsers();
  }, []);

  // Load graph data for selected user
  useEffect(() => {
    if (!selectedUserId) return;
    const fetchGraph = async () => {
      setLoadingGraph(true);
      setSelectedNode(null);
      try {
        const data = await api.get(`/api/admin/profile-graph/${selectedUserId}/`);
        // Assign random initial coords
        const initializedNodes = (data.nodes || []).map((node: GraphNode) => ({
          ...node,
          x: 200 + Math.random() * 200,
          y: 150 + Math.random() * 150,
          vx: 0,
          vy: 0
        }));
        nodesRef.current = initializedNodes;
        linksRef.current = data.links || [];
        setGraphData({
          user_id: data.user_id,
          nodes: initializedNodes,
          links: data.links || []
        });
      } catch (err) {
        console.error('Failed to load user graph', err);
      } finally {
        setLoadingGraph(false);
      }
    };
    fetchGraph();
  }, [selectedUserId]);

  // Load all leads when switching to leads tab
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
    if (activeTab === 'leads') {
      fetchLeads();
    }
  }, [activeTab, leadCategoryFilter, leadStatusFilter]);

  // Canvas force-directed simulation
  useEffect(() => {
    if (activeTab !== 'graph' || !canvasRef.current || nodesRef.current.length === 0) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resizeCanvas = () => {
      const rect = canvas.parentElement?.getBoundingClientRect();
      canvas.width = rect?.width || 600;
      canvas.height = rect?.height || 450;
    };
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    // Physics parameters
    const kAttract = 0.003;
    const kRepel = 2800;
    const gravity = 0.05;
    const friction = 0.85;
    const restLength = 120;

    const tick = () => {
      const width = canvas.width;
      const height = canvas.height;
      const center = { x: width / 2, y: height / 2 };
      const nodes = nodesRef.current;
      const links = linksRef.current;

      // 1. Reset forces & Repulsion (Coulomb law)
      for (let i = 0; i < nodes.length; i++) {
        const n1 = nodes[i];
        if (!n1.x || !n1.y) continue;
        
        let fx = 0;
        let fy = 0;

        // Repel from other nodes
        for (let j = 0; j < nodes.length; j++) {
          if (i === j) continue;
          const n2 = nodes[j];
          if (!n2.x || !n2.y) continue;

          const dx = n1.x - n2.x;
          const dy = n1.y - n2.y;
          const distSq = dx * dx + dy * dy + 0.1;
          const dist = Math.sqrt(distSq);
          
          if (dist < 400) {
            const force = kRepel / distSq;
            fx += (dx / dist) * force;
            fy += (dy / dist) * force;
          }
        }

        // Central gravity
        fx += (center.x - n1.x) * gravity;
        fy += (center.y - n1.y) * gravity;

        n1.vx = ((n1.vx || 0) + fx) * friction;
        n1.vy = ((n1.vy || 0) + fy) * friction;
      }

      // 2. Attraction (Hooke's law for connected edges)
      for (const link of links) {
        const sNode = nodes.find(n => n.id === link.source);
        const tNode = nodes.find(n => n.id === link.target);
        if (!sNode || !tNode || !sNode.x || !sNode.y || !tNode.x || !tNode.y) continue;

        const dx = tNode.x - sNode.x;
        const dy = tNode.y - sNode.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.1;
        const disp = dist - restLength;
        
        const f = disp * kAttract;
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;

        sNode.vx = (sNode.vx || 0) + fx;
        sNode.vy = (sNode.vy || 0) + fy;
        tNode.vx = (tNode.vx || 0) - fx;
        tNode.vy = (tNode.vy || 0) - fy;
      }

      // 3. Update positions
      for (const node of nodes) {
        if (node === draggingNodeRef.current) continue;
        node.x = (node.x || 0) + (node.vx || 0);
        node.y = (node.y || 0) + (node.vy || 0);

        // Keep inside bounds
        node.x = Math.max(30, Math.min(width - 30, node.x));
        node.y = Math.max(30, Math.min(height - 30, node.y));
      }

      // 4. Drawing operations
      // Draw dark background with subtle grid
      ctx.fillStyle = '#05030A';
      ctx.fillRect(0, 0, width, height);

      // Draw subtle grid
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.03)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = 0; x < width; x += 40) {
        ctx.moveTo(x, 0); ctx.lineTo(x, height);
      }
      for (let y = 0; y < height; y += 40) {
        ctx.moveTo(0, y); ctx.lineTo(width, y);
      }
      ctx.stroke();

      const hoveredNode = hoveredNodeRef.current;
      const activeNode = selectedNode || hoveredNode;
      
      // Determine connected nodes if there's an active node
      const connectedNodeIds = new Set<string>();
      const connectedLinkIndices = new Set<number>();
      if (activeNode) {
        connectedNodeIds.add(activeNode.id);
        links.forEach((link, i) => {
          if (link.source === activeNode.id || link.target === activeNode.id) {
            connectedNodeIds.add(link.source);
            connectedNodeIds.add(link.target);
            connectedLinkIndices.add(i);
          }
        });
      }

      // Draw Edges
      links.forEach((link, i) => {
        const s = nodes.find(n => n.id === link.source);
        const t = nodes.find(n => n.id === link.target);
        if (!s || !t || !s.x || !s.y || !t.x || !t.y) return;

        const isConnected = activeNode ? connectedLinkIndices.has(i) : true;
        const alpha = isConnected ? 0.6 : 0.05;

        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(t.x, t.y);
        
        // Edge styling
        ctx.lineWidth = isConnected ? 2 : 1;
        ctx.strokeStyle = `rgba(255, 255, 255, ${alpha})`;
        ctx.stroke();

        // Draw relationship type text in middle
        if (isConnected || !activeNode) {
          const midX = (s.x + t.x) / 2;
          const midY = (s.y + t.y) / 2;
          ctx.save();
          ctx.translate(midX, midY);
          
          // Background pill for text
          const text = link.type;
          ctx.font = 'bold 10px Inter, sans-serif';
          const textWidth = ctx.measureText(text).width;
          ctx.fillStyle = `rgba(15, 11, 20, ${isConnected ? 0.9 : 0.6})`;
          ctx.beginPath();
          ctx.roundRect(-textWidth/2 - 4, -8, textWidth + 8, 16, 8);
          ctx.fill();
          
          ctx.fillStyle = `rgba(255, 255, 255, ${isConnected ? 0.9 : 0.4})`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(text, 0, 0);
          ctx.restore();
        }
      });

      // Draw Nodes
      for (const node of nodes) {
        if (!node.x || !node.y) continue;

        const isHovered = hoveredNode?.id === node.id;
        const isSelected = selectedNode?.id === node.id;
        const isActive = isHovered || isSelected;
        const isConnected = activeNode ? connectedNodeIds.has(node.id) : true;
        
        const baseColor = TYPE_COLORS[node.type] || TYPE_COLORS.Default;
        const radius = node.type === 'User' ? 24 : 16;
        const drawRadius = isActive ? radius * 1.1 : radius;
        
        const alpha = isConnected ? 1 : 0.2;

        ctx.save();
        ctx.globalAlpha = alpha;

        // Glowing circle effect for connected nodes
        if (isConnected) {
          ctx.shadowBlur = isActive ? 30 : 15;
          ctx.shadowColor = baseColor;
        }

        // Create gradient for 3D orb effect
        const grad = ctx.createRadialGradient(
          node.x - drawRadius*0.3, node.y - drawRadius*0.3, 0,
          node.x, node.y, drawRadius
        );
        grad.addColorStop(0, '#ffffff');
        grad.addColorStop(0.2, baseColor);
        grad.addColorStop(1, '#000000');

        ctx.beginPath();
        ctx.arc(node.x, node.y, drawRadius, 0, 2 * Math.PI);
        ctx.fillStyle = grad;
        ctx.fill();

        // Inner highlight ring
        ctx.beginPath();
        ctx.arc(node.x, node.y, drawRadius - 1.5, 0, 2 * Math.PI);
        ctx.strokeStyle = `rgba(255,255,255,${isActive ? 0.5 : 0.2})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();

        ctx.restore();
        
        ctx.save();
        ctx.globalAlpha = alpha;
        // Draw node label above it
        ctx.shadowBlur = 0;
        ctx.font = node.type === 'User' ? 'bold 13px Inter, sans-serif' : '500 12px Inter, sans-serif';
        
        // Background for text to improve legibility
        const labelWidth = ctx.measureText(node.label).width;
        ctx.fillStyle = 'rgba(5, 3, 10, 0.7)';
        ctx.fillRect(node.x - labelWidth/2 - 4, node.y - drawRadius - 22, labelWidth + 8, 18);
        
        ctx.fillStyle = isActive ? '#ffffff' : '#e2e8f0';
        ctx.textAlign = 'center';
        ctx.fillText(node.label, node.x, node.y - drawRadius - 8);

        // Icon indicator inside node
        ctx.font = 'bold 12px monospace';
        ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(node.type[0], node.x, node.y);

        ctx.restore();
      }

      animationRef.current = requestAnimationFrame(tick);
    };

    tick();

    return () => {
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
      window.removeEventListener('resize', resizeCanvas);
    };
  }, [graphData, activeTab, selectedNode]);

  // Drag and Drop handlers
  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    // Find node under mouse
    const clicked = nodesRef.current.find(n => {
      if (!n.x || !n.y) return false;
      const dx = n.x - mouseX;
      const dy = n.y - mouseY;
      const radius = n.type === 'User' ? 24 : 16;
      return dx * dx + dy * dy < radius * radius * 1.5;
    });

    if (clicked) {
      draggingNodeRef.current = clicked;
      setSelectedNode(clicked);
    } else {
      setSelectedNode(null);
    }
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    if (draggingNodeRef.current) {
      draggingNodeRef.current.x = mouseX;
      draggingNodeRef.current.y = mouseY;
      return;
    }

    // Hover logic
    const hovered = nodesRef.current.find(n => {
      if (!n.x || !n.y) return false;
      const dx = n.x - mouseX;
      const dy = n.y - mouseY;
      const radius = n.type === 'User' ? 24 : 16;
      return dx * dx + dy * dy < radius * radius * 1.5;
    });

    if (hovered !== hoveredNodeRef.current) {
      hoveredNodeRef.current = hovered || null;
      canvasRef.current.style.cursor = hovered ? 'pointer' : 'grab';
    }
  };

  const handleMouseUp = () => {
    draggingNodeRef.current = null;
  };

  return (
    <div className="fixed inset-0 bg-[#070105]/95 z-50 flex flex-col font-sans text-gray-200">
      {/* Header */}
      <header className="h-16 border-b border-border bg-surface/30 backdrop-blur-md px-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-accent rounded-xl flex items-center justify-center text-gray-950 font-bold shadow-md shadow-accent/20">
            <Layers size={18} />
          </div>
          <div>
            <h1 className="text-md font-bold tracking-wider uppercase text-white flex items-center gap-2">
              Knowledge Graph Admin
              <span className="text-[10px] bg-red-500/20 text-red-400 font-bold px-2 py-0.5 rounded-full border border-red-500/30">
                PROD
              </span>
            </h1>
            <p className="text-[10px] text-gray-400 font-mono">Tenant: bhavesh-dripzy/companion</p>
          </div>
        </div>

        {/* Tab switchers */}
        <div className="flex bg-surface-elevated/50 p-1 rounded-xl border border-border">
          <button
            onClick={() => setActiveTab('graph')}
            className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all ${
              activeTab === 'graph' 
                ? 'bg-accent text-gray-950 shadow-sm' 
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Graph Explorer
          </button>
          <button
            onClick={() => setActiveTab('leads')}
            className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all ${
              activeTab === 'leads' 
                ? 'bg-accent text-gray-950 shadow-sm' 
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Commercial Leads
          </button>
        </div>

        <button
          onClick={onClose}
          className="text-xs font-bold text-gray-400 hover:text-white border border-border hover:bg-surface px-3 py-1.5 rounded-lg transition-all"
        >
          Exit Dashboard
        </button>
      </header>

      {/* Main Workspace */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Sidebar: User Directory */}
        <div className="w-64 border-r border-border bg-[#0e0c10]/40 flex flex-col">
          <div className="p-4 border-b border-border flex items-center justify-between">
            <span className="text-xs font-bold text-gray-300 flex items-center gap-1.5">
              <Users size={14} className="text-accent" />
              Users Directory
            </span>
            <span className="text-[10px] text-gray-500 font-mono">Count: {users.length}</span>
          </div>

          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {users.map(u => (
              <button
                key={u.id}
                onClick={() => setSelectedUserId(u.id)}
                className={`w-full text-left p-2.5 rounded-lg border text-xs transition-all flex flex-col gap-1 ${
                  selectedUserId === u.id
                    ? 'bg-accent-soft border-accent/40 text-amber-300'
                    : 'border-transparent text-gray-400 hover:bg-surface/30 hover:text-white'
                }`}
              >
                <div className="flex items-center justify-between w-full">
                  <span className="font-bold truncate max-w-[120px]">{u.name || u.id}</span>
                  <span className="text-[9px] text-gray-500 font-mono truncate">{u.id}</span>
                </div>
                {u.city && (
                  <span className="text-[10px] text-gray-500 flex items-center gap-0.5 font-medium">
                    <MapPin size={10} />
                    {u.city}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Center Panel (Explorer or Lead Tracker) */}
        <div className="flex-1 flex flex-col bg-background/50 overflow-hidden relative">
          {activeTab === 'graph' ? (
            <div className="flex-1 flex flex-col overflow-hidden">
              {/* Legend bar */}
              <div className="h-10 border-b border-border bg-surface/10 px-6 flex items-center gap-4 text-xs text-gray-400">
                <span className="font-medium text-gray-300">Nodes:</span>
                {Object.entries(TYPE_COLORS).filter(([k]) => k !== 'Default').map(([type, color]) => (
                  <div key={type} className="flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }}></span>
                    <span>{type}</span>
                  </div>
                ))}
              </div>

              {/* Graph canvas */}
              <div className="flex-1 relative bg-gradient-to-b from-transparent to-[#0f0b14]/30">
                {loadingGraph && (
                  <div className="absolute inset-0 bg-[#0c0008]/40 backdrop-blur-xs flex items-center justify-center z-10">
                    <div className="flex flex-col items-center gap-2">
                      <RefreshCw size={24} className="text-accent animate-spin" />
                      <span className="text-xs text-gray-400 font-mono">Syncing Graph Topology...</span>
                    </div>
                  </div>
                )}
                
                <canvas
                  ref={canvasRef}
                  onMouseDown={handleMouseDown}
                  onMouseMove={handleMouseMove}
                  onMouseUp={handleMouseUp}
                  onMouseLeave={handleMouseUp}
                  className="w-full h-full cursor-grab active:cursor-grabbing"
                />

                {!loadingGraph && nodesRef.current.length === 0 && (
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className="text-xs text-gray-500 font-mono">No nodes found for this profile in Neo4j.</span>
                  </div>
                )}
              </div>
            </div>
          ) : (
            /* Lead list panel */
            <div className="flex-1 flex flex-col overflow-hidden p-6 space-y-4">
              {/* Lead filters */}
              <div className="flex gap-4 items-center bg-surface/30 p-3 rounded-xl border border-border">
                <div className="flex flex-col gap-1">
                  <label className="text-[10px] text-gray-400 font-bold uppercase tracking-wider">Category</label>
                  <div className="relative">
                    <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" />
                    <input
                      type="text"
                      placeholder="Filter category (e.g. food)..."
                      value={leadCategoryFilter}
                      onChange={e => setLeadCategoryFilter(e.target.value)}
                      className="bg-[#18161b] border border-border rounded-lg pl-8 pr-3 py-1 text-xs text-white focus:outline-none focus:border-accent w-48"
                    />
                  </div>
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-[10px] text-gray-400 font-bold uppercase tracking-wider">Status</label>
                  <select
                    value={leadStatusFilter}
                    onChange={e => setLeadStatusFilter(e.target.value)}
                    className="bg-[#18161b] border border-border rounded-lg px-2 py-1 text-xs text-white focus:outline-none focus:border-accent"
                  >
                    <option value="open">Open</option>
                    <option value="contacted">Contacted</option>
                    <option value="converted">Converted</option>
                    <option value="archived">Archived</option>
                  </select>
                </div>

                <button
                  onClick={fetchLeads}
                  className="h-8 border border-border hover:bg-surface self-end px-3 rounded-lg text-xs flex items-center gap-1.5 font-bold transition-all text-gray-300"
                >
                  <RefreshCw size={12} className={loadingLeads ? 'animate-spin' : ''} />
                  Refresh
                </button>
              </div>

              {/* Leads Table */}
              <div className="flex-1 bg-[#100e12]/60 border border-border rounded-xl overflow-hidden flex flex-col">
                <div className="flex-1 overflow-auto">
                  {loadingLeads ? (
                    <div className="w-full h-full flex items-center justify-center">
                      <RefreshCw size={24} className="text-accent animate-spin" />
                    </div>
                  ) : leads.length === 0 ? (
                    <div className="w-full h-full flex items-center justify-center text-xs text-gray-500 font-mono">
                      No matching commercial leads found.
                    </div>
                  ) : (
                    <table className="w-full text-left text-xs border-collapse">
                      <thead>
                        <tr className="bg-surface/40 border-b border-border text-gray-400 font-bold">
                          <th className="p-3">Lead Product/Service</th>
                          <th className="p-3">Category</th>
                          <th className="p-3">Budget</th>
                          <th className="p-3">Intent Score</th>
                          <th className="p-3">Consent</th>
                          <th className="p-3">Last Seen</th>
                          <th className="p-3">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border/50">
                        {leads.map(l => (
                          <tr key={l.lead_id} className="hover:bg-surface/10 transition-colors">
                            <td className="p-3 font-bold text-white flex flex-col gap-0.5">
                              <span>{l.product}</span>
                              <span className="text-[10px] text-gray-500 font-mono font-normal">ID: {l.lead_id.substring(0, 16)}...</span>
                            </td>
                            <td className="p-3">
                              <span className="bg-purple-500/10 text-purple-400 border border-purple-500/20 px-2 py-0.5 rounded-full font-semibold uppercase text-[9px] tracking-wider">
                                {l.category}
                              </span>
                            </td>
                            <td className="p-3">
                              <span className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider ${
                                l.budget_signal === 'high' 
                                  ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' 
                                  : l.budget_signal === 'mid' 
                                  ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                                  : 'bg-gray-500/20 text-gray-400 border border-gray-500/30'
                              }`}>
                                {l.budget_signal || 'unknown'}
                              </span>
                            </td>
                            <td className="p-3 font-mono font-bold">
                              <div className="flex items-center gap-1.5">
                                <span className={l.intent_score && l.intent_score >= 0.8 ? 'text-red-400' : 'text-gray-300'}>
                                  {l.intent_score !== null ? l.intent_score.toFixed(2) : 'N/A'}
                                </span>
                                <div className="w-12 bg-surface rounded-full h-1.5 overflow-hidden">
                                  <div 
                                    className="bg-accent h-full" 
                                    style={{ width: `${(l.intent_score || 0) * 100}%` }}
                                  ></div>
                                </div>
                              </div>
                            </td>
                            <td className="p-3">
                              <span className="text-[10px] text-gray-400 capitalize font-medium">{l.consent_status}</span>
                            </td>
                            <td className="p-3 text-gray-400 font-mono text-[10px]">
                              {l.last_seen_at ? new Date(l.last_seen_at).toLocaleDateString() : 'N/A'}
                            </td>
                            <td className="p-3">
                              <button 
                                onClick={() => {
                                  // Find the simulated node and view its properties
                                  setActiveTab('graph');
                                  setSelectedUserId(l.user_id);
                                  // Schedule node lookup once nodes load
                                  setTimeout(() => {
                                    const match = nodesRef.current.find(n => n.id === l.lead_id);
                                    if (match) setSelectedNode(match);
                                  }, 500);
                                }}
                                className="text-accent hover:underline font-bold text-[10px] flex items-center gap-0.5"
                              >
                                View Node
                                <ChevronRight size={10} />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Right Sidebar: Details Drawer */}
        <div className="w-80 border-l border-border bg-[#0e0c10]/40 flex flex-col overflow-y-auto">
          <div className="p-4 border-b border-border flex items-center gap-1.5">
            <Info size={14} className="text-accent" />
            <span className="text-xs font-bold text-gray-300">Properties &amp; Meta</span>
          </div>

          {selectedNode ? (
            <div className="p-4 space-y-5">
              {/* Type tag */}
              <div className="flex flex-col gap-1">
                <span className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">Node Classification</span>
                <div className="flex items-center gap-2">
                  <span 
                    className="w-3 h-3 rounded-full" 
                    style={{ backgroundColor: TYPE_COLORS[selectedNode.type] || TYPE_COLORS.Default }}
                  ></span>
                  <span className="text-sm font-bold text-white uppercase tracking-wide">
                    {selectedNode.type}
                  </span>
                </div>
                <span className="text-[10px] text-gray-400 italic">
                  {TYPE_LABELS[selectedNode.type] || 'Graph Attribute'}
                </span>
              </div>

              {/* Label */}
              <div className="flex flex-col gap-1">
                <span className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">Primary Display</span>
                <span className="text-sm text-white font-bold bg-[#151219] p-2.5 rounded-lg border border-border break-all">
                  {selectedNode.label}
                </span>
              </div>

              {/* ID */}
              <div className="flex flex-col gap-1">
                <span className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">System Identifier</span>
                <span className="text-[10px] font-mono text-gray-400 bg-[#151219] p-2 rounded-lg border border-border select-all truncate">
                  {selectedNode.id}
                </span>
              </div>

              {/* Properties list */}
              <div className="flex flex-col gap-2">
                <span className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">Metadata Fields</span>
                <div className="bg-[#151219]/60 border border-border rounded-lg divide-y divide-border/40 overflow-hidden">
                  {Object.entries(selectedNode.properties || {}).length === 0 ? (
                    <div className="p-3 text-[10px] text-gray-500 font-mono italic">No payload parameters.</div>
                  ) : (
                    Object.entries(selectedNode.properties).map(([k, v]) => (
                      <div key={k} className="p-2.5 flex flex-col gap-1 text-[11px]">
                        <span className="font-mono text-gray-400 font-bold">{k}</span>
                        <span className="font-mono text-white bg-[#0e0c10] p-1.5 rounded border border-border/20 break-all whitespace-pre-wrap">
                          {typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v)}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-6 text-center text-xs text-gray-500 font-mono space-y-2">
              <Sparkles size={20} className="text-gray-600 animate-pulse" />
              <span>Select a node in the graph explorer to inspect its knowledge payload.</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
