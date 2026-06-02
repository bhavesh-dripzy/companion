/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { LeftSidebar } from "./components/LeftSidebar";
import { ChatArea } from "./components/ChatArea";
import { RemindersPopup } from "./components/RemindersPopup";
import { OrdersPopup } from "./components/OrdersPopup";
import { AnimatePresence } from "motion/react";
import { initialMcpServers } from "./data/mcpServers";
import { ChatMessage, McpServer, SystemLog } from "./types";
import { Shield, Sparkles, Terminal, Info, X, Globe, Cpu, Settings, CheckCircle2, RotateCw, Unplug, Check, AlertTriangle, Bell, ShoppingBag, Clock } from "lucide-react";

export default function App() {
  const [servers, setServers] = useState<McpServer[]>(initialMcpServers);
  const [selectedServer, setSelectedServer] = useState<McpServer | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [logs, setLogs] = useState<SystemLog[]>([]);
  const [isPending, setIsPending] = useState(false);
  const [sysStatus, setSysStatus] = useState({
    geminiConnected: false,
    environment: "development",
    hasApiKey: false,
  });

  // Modal tab system
  const [activeModalTab, setActiveModalTab] = useState<"mcp" | "integrations" | "preferences">("mcp");
  const [isRotating, setIsRotating] = useState(false);

  // Modal system overlays to prompt integrations & system preferences
  const [showNotification, setShowNotification] = useState<{
    visible: boolean;
    title: string;
    message: string;
    type: "info" | "success" | "warn";
  }>({
    visible: false,
    title: "",
    message: "",
    type: "info"
  });

  const [mobileShowLeft, setMobileShowLeft] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);

  // Lifted custom dialog overlay states and data for Reminders & Orders
  const [showRemindersPopup, setShowRemindersPopup] = useState(false);
  const [showOrdersPopup, setShowOrdersPopup] = useState(false);

  // Prepopulated interactive reminders list with "daily" and "today" categories
  const [reminders, setReminders] = useState([
    { id: "rem-d1", category: "daily", text: "Take daily vitamins & morning supplements", time: "09:00 AM", completed: false },
    { id: "rem-d2", category: "daily", text: "Postural posture stretch & hydration check", time: "Every 2 hrs", completed: false },
    { id: "rem-d3", category: "daily", text: "Review evening workout session and wellness log", time: "06:30 PM", completed: true },
    { id: "rem-t1", category: "today", text: "Confirm meeting with financial advisor", time: "02:00 PM", completed: false },
    { id: "rem-t2", category: "today", text: "Track grocery restock through Blinkit/Zepto", time: "04:00 PM", completed: false },
    { id: "rem-t3", category: "today", text: "Submit car registration insurance renewals", time: "Today", completed: false },
  ]);

  // Prepopulated interactive orders list with "daily" (recurring) and "today" (one-time) categories
  const [orders, setOrders] = useState([
    { id: "ord-d1", category: "daily", provider: "Swiggy Daily", text: "Fresh low-fat milk bottle & organic whole bread", status: "Active Subscription", time: "Daily 7 AM", price: "₹120" },
    { id: "ord-d2", category: "daily", provider: "Fruits Routine", text: "Seasonal mixed seed & avocado fitness combo", status: "Paused", time: "Daily 9 AM", price: "₹240" },
    { id: "ord-t1", category: "today", provider: "Zomato Food", text: "Sourdough Woodfired Pepperoni Pizza x1", status: "In Transit", time: "ETA 12 mins", price: "₹480" },
    { id: "ord-t2", category: "today", provider: "Zepto Quick", text: "Salted Butter, Fresh Bananas & Greek Yogurt", status: "Preparing", time: "ETA 8 mins", price: "₹210" },
    { id: "ord-t3", category: "today", provider: "Blinkit Spot", text: "Emergency charging cord & cell lithium batteries", status: "Delivered", time: "Delivered today", price: "₹350" },
  ]);

  const handleToggleReminder = (id: string) => {
    setReminders(prev => prev.map(r => r.id === id ? { ...r, completed: !r.completed } : r));
    appLog("info", `Toggled reminder task status.`);
  };

  const handleAddReminder = (text: string, category: "daily" | "today") => {
    const newRem = {
      id: `rem-${Date.now()}`,
      category,
      text,
      time: category === "daily" ? "08:30 AM" : "Just now",
      completed: false
    };
    setReminders(prev => [...prev, newRem]);
    appLog("success", `Appended new reminder task under "${category}."`);
  };

  const handleDeleteReminder = (id: string) => {
    setReminders(prev => prev.filter(r => r.id !== id));
    appLog("info", `Deleted reminder task.`);
  };

  const handleClearCompletedReminders = () => {
    setReminders(prev => prev.filter(r => !r.completed));
    appLog("info", `Cleared all completed reminders.`);
  };

  const handleCancelOrder = (id: string) => {
    setOrders(prev => {
      return prev.map(o => {
        if (o.id === id) {
          const nextStatus = o.status === "Cancelled" ? "Preparing" : "Cancelled";
          return { ...o, status: nextStatus };
        }
        return o;
      });
    });
    appLog("info", `Toggled order/subscription status.`);
  };

  const handleAddOrder = (provider: string, text: string, category: "daily" | "today", price: string) => {
    const newOrd = {
      id: `ord-${Date.now()}`,
      category,
      provider,
      text,
      status: category === "daily" ? "Active Subscription" : "Preparing",
      time: category === "daily" ? "Daily 10 AM" : "ETA 12 mins",
      price: price || "₹150"
    };
    setOrders(prev => [...prev, newOrd]);
    appLog("success", `Placed direct checkout simulation through ${provider}.`);
  };

  const handleDeleteOrder = (id: string) => {
    setOrders(prev => prev.filter(o => o.id !== id));
    appLog("info", `Deleted order tracker.`);
  };

  const handleToggleSidebar = () => {
    if (window.innerWidth < 1024) {
      setMobileShowLeft(prev => !prev);
    } else {
      setIsSidebarCollapsed(prev => !prev);
    }
  };

  // Quick helper to insert console logs
  const appLog = (type: SystemLog["type"], message: string, details?: string) => {
    const newLog: SystemLog = {
      id: "LOG-" + Date.now() + Math.random(),
      timestamp: new Date().toLocaleTimeString(),
      type,
      message,
      details,
    };
    setLogs((prev) => [newLog, ...prev]);
  };

  // Fetch initial connection status from full-stack system backend
  useEffect(() => {
    appLog("info", "Companion virtual chassis booting up...");
    
    fetch("/api/status")
      .then((res) => res.json())
      .then((data) => {
        setSysStatus(data);
        if (data.geminiConnected) {
          appLog(
            "success",
            "Gemini cognitive terminal established on server port 3000.",
            `Agent engine is active (model: gemini-3.5-flash). Authorization token is verified.`
          );
        } else {
          appLog(
            "info",
            "No active Gemini key set. Companion running on fully functional tool call sandbox simulation.",
            "Secrets are managed via the Settings menu in AI Studio. Running local parsing interceptor rules."
          );
        }
      })
      .catch((err) => {
        appLog("error", "Failed to reach system node status checkpoint.", err.message);
      });
  }, []);

  // Connected status toggler
  const handleToggleServer = (serverId: string) => {
    setServers((prevEngines) => {
      const updated = prevEngines.map((srv) => {
        if (srv.id === serverId) {
          const nextState = !srv.connected;
          appLog(
            nextState ? "mcp_connect" : "info",
            `${srv.name} Model Context Protocol connection status flipped: ${
              nextState ? "ONLINE" : "OFFLINE"
            }`,
            nextState
              ? `Registered ${srv.tools.length} active RPC functions to unified cognitive router.\nDescription: ${srv.description}`
              : `Flushed all tools associated with ${srv.name} out of Gemini active session.`
          );
          
          // Keep current inspector state in sync with updated list
          if (selectedServer?.id === serverId) {
            setSelectedServer({ ...srv, connected: nextState });
          }
          return { ...srv, connected: nextState };
        }
        return srv;
      });
      return updated;
    });
  };

  const handleSelectServer = (server: McpServer | null) => {
    setSelectedServer(server);
    if (server) {
      appLog("info", `Inspecting registry schema details for: ${server.name}`, `Available client schema endpoints: ${JSON.stringify(server.tools, null, 2)}`);
    }
  };

  // Dispatch message to full-stack Gemini agent
  const handleSendMessage = async (rawText: string) => {
    const userMsg: ChatMessage = {
      id: "MSG-" + Date.now() + "-U",
      sender: "user",
      text: rawText,
      timestamp: new Date().toLocaleTimeString(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setIsPending(true);
    appLog("info", `User input sent: "${rawText}"`);

    // Compile active tool schemas from connected servers
    const activeTools = servers
      .filter((s) => s.connected)
      .flatMap((s) => s.tools);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          messages: messages,
          activeTools: activeTools,
          userMessage: rawText,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP network response code: ${response.status}`);
      }

      const data = await response.json();

      // Append backend step tracing logs automatically
      if (data.logs && data.logs.length > 0) {
        const compiledLogs: SystemLog[] = data.logs.map((el: any) => ({
          id: "LOG-" + Math.random() + "-" + Date.now(),
          timestamp: el.timestamp || new Date().toLocaleTimeString(),
          type: el.type,
          message: el.message,
          details: el.details,
        }));
        // Merge logs
        setLogs((prev) => [...compiledLogs, ...prev]);
      }

      // Add assistant response message
      const botMsg: ChatMessage = {
        id: "MSG-" + Date.now() + "-A",
        sender: "assistant",
        text: data.text,
        timestamp: new Date().toLocaleTimeString(),
        ...(data.mcpCall ? { mcpCall: data.mcpCall } : {}),
      };

      setMessages((prev) => [...prev, botMsg]);
      setIsPending(false);

      if (data.mcpCall) {
        appLog(
          "success",
          `Model Context Protocol Tool executed: ${data.mcpCall.toolName}`,
          `Payload outcome:\n${JSON.stringify(data.mcpCall, null, 2)}`
        );
      }
    } catch (err: any) {
      console.error("Chat dispatch system issue:", err);
      appLog("error", "Exception caught while processing cognitive response.", err.message || err);
      
      const errorMsg: ChatMessage = {
        id: "MSG-" + Date.now() + "-ERR",
        sender: "assistant",
        text: `⚠️ **Cognitive Engine Disrupted**: I faced an issue compiling context. \n\n*Details: ${err.message || err}* \n\nEnsure that the Express server container is starting correctly and is accessible.`,
        timestamp: new Date().toLocaleTimeString(),
      };
      setMessages((prev) => [...prev, errorMsg]);
      setIsPending(false);
    }
  };

  // Suggested Chips & Popular Ideas handler
  const handleSuggestionClick = (keywordOrPrompt: string) => {
    // If it is a full prompt string or question, dispatch immediately
    if (keywordOrPrompt.includes(" ") || keywordOrPrompt.includes("?")) {
      handleSendMessage(keywordOrPrompt);
      return;
    }

    let fillPrompt = "";
    if (keywordOrPrompt === "Biryani") {
      fillPrompt = "Find lucknowi chicken biryani on Zomato and show menu details";
    } else if (keywordOrPrompt === "Pizza") {
      fillPrompt = "Search pizza restaurants available near Bangalore on Zomato";
    } else if (keywordOrPrompt === "Groceries") {
      fillPrompt = "Is milk in stock on Swiggy Instamart?";
    } else if (keywordOrPrompt === "Today's ETA") {
      fillPrompt = "What events are happening in Indiranagar zip 560038 today?";
    }
    
    if (fillPrompt) {
      handleSendMessage(fillPrompt);
    }
  };

  const handleSelectThread = (threadMessages: ChatMessage[], title: string) => {
    setMessages(threadMessages);
    appLog("success", `Conversation parsed & loaded: "${title}"`, `Synchronized historic telemetry context into session state.`);
    setShowNotification({
      visible: true,
      title: "Thread Loaded",
      message: `Historical context for "${title}" has been successfully restored.`,
      type: "success"
    });
  };

  const handleClearMessages = () => {
    setMessages([]);
    appLog("success", "Clean slate conversation thread initiated.");
  };

  // Google Maps computed actions helper
  const handleMapAction = () => {
    appLog("info", "Triggering active Google Maps API calculation...", "Origin: Indiranagar Office Block\nDestination: Zepto Dark Store #10\nETA constraints: peak traffic flow loaded");
    
    setTimeout(() => {
      setShowNotification({
        visible: true,
        title: "Google Maps Router",
        message: "Successfully computed shipping ETA. Distance: 4.8 km | ETA: 12 minutes (Standard transit routing active).",
        type: "success"
      });
      appLog("success", "Google Maps telemetry returned static payload: 4.8km, 12 mins.");
    }, 600);
  };

  // Google Calendar integration reconnection helper
  const handleCalendarAction = () => {
    appLog("info", "Initiating OAuth credentials synchronization check for Google Calendar API...");
    
    setTimeout(() => {
      setShowNotification({
        visible: true,
        title: "Google Calendar Sync",
        message: "Google Workspace single-sign OAuth keys aligned and synchronized successfully. 3 agenda blocks loaded.",
        type: "success"
      });
      appLog("success", "Google Calendar credentials reset. Connection State: Active.");
    }, 800);
  };

  const activeServersCount = servers.filter((s) => s.connected).length;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-950 font-sans select-none antialiased relative">
      {/* Mobile left sidebar background backdrop */}
      {mobileShowLeft && (
        <div
          className="fixed inset-0 bg-slate-950/70 backdrop-blur-xs z-40 lg:hidden"
          onClick={() => setMobileShowLeft(false)}
        />
      )}

      {/* Left Unified Sidebar wrapper for responsiveness */}
      <div className={`
        fixed inset-y-0 left-0 z-50 transform lg:relative transition-all duration-300 ease-in-out flex shrink-0 h-full overflow-hidden
        ${isSidebarCollapsed ? "w-0 opacity-0 -translate-x-full lg:w-0 lg:-translate-x-full" : "w-80 opacity-100"}
        ${mobileShowLeft ? "translate-x-0 opacity-100" : !isSidebarCollapsed ? "-translate-x-full lg:translate-x-0" : "-translate-x-full"}
      `}>
        <LeftSidebar
          servers={servers}
          onToggleServer={handleToggleServer}
          onSelectServer={handleSelectServer}
          selectedServer={selectedServer}
          onTriggerMapAction={handleMapAction}
          onTriggerCalendarAction={handleCalendarAction}
          onAddStream={() => {
            appLog("info", "Opening live MCP connector portal. Choose custom JSON server endpoint schema.");
            setShowNotification({
              visible: true,
              title: "Connect Custom MCP",
              message: "To register a custom Model Context Protocol server, ensure it respects JSON RPC specification and configure its launch script in process settings.",
              type: "info"
            });
            setMobileShowLeft(false);
          }}
          onExplore={() => {
            setSelectedServer(null);
            setMobileShowLeft(false);
          }}
          openSettings={() => {
            setShowNotification({
              visible: true,
              title: "Companion Core Status",
              message: `Node: Express Fullstack v4\nCognitive Backend: gemini-3.5-flash\nSecrets Token: ${sysStatus.hasApiKey ? "ACTIVE" : "SANDBOX_SIMULATED"}\nPort allocation: Port 3000`,
              type: "info"
            });
            setMobileShowLeft(false);
          }}
          onClearMessages={handleClearMessages}
          onSelectThread={handleSelectThread}
          sysStatus={sysStatus}
          isSidebarCollapsed={isSidebarCollapsed}
          onToggleCollapse={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
          onShowReminders={() => setShowRemindersPopup(true)}
          onShowOrders={() => setShowOrdersPopup(true)}
        />
      </div>

      {/* Center Canvas / Gradient Chat Viewport */}
      <ChatArea
        messages={messages}
        onSendMessage={handleSendMessage}
        isPending={isPending}
        logs={logs}
        onSuggestionClick={handleSuggestionClick}
        onToggleLeft={handleToggleSidebar}
        connectedCount={activeServersCount}
        isSidebarCollapsed={isSidebarCollapsed}
      />

      {/* High-fidelity Connected Apps & Systems Modal Dialog matching exact user screenshot */}
      {selectedServer && (
        <div className="fixed inset-0 z-100 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm p-4">
          <div className="bg-white w-full max-w-4xl h-[600px] rounded-3xl overflow-hidden shadow-2xl flex border border-[#d5e4f3] animate-in fade-in zoom-in-95 duration-200 relative select-none">
            {/* Close modal absolute button */}
            <button
              onClick={() => setSelectedServer(null)}
              className="absolute top-4.5 right-4.5 p-1.5 hover:bg-slate-100 rounded-lg text-slate-400 hover:text-slate-800 transition-colors z-20 cursor-pointer"
              title="Close Settings"
            >
              <X className="w-5 h-5 stroke-[2.2]" />
            </button>

            {/* Left Nav Pane */}
            <div className="w-[240px] bg-[#f8fafc] border-r border-[#d5e4f3]/60 p-4.5 flex flex-col justify-between">
              <div className="space-y-4 pt-1">
                {/* Optional Title */}
                <div className="px-1 bg-slate-200/50 py-1.5 rounded-lg text-center mb-1">
                  <span className="text-[10px] uppercase font-mono font-extrabold text-slate-500 tracking-wider">
                    Companion Dashboard
                  </span>
                </div>

                {/* Navigation items in sidebar */}
                <div className="space-y-1">
                  <button
                    onClick={() => setActiveModalTab("mcp")}
                    className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-[13px] font-bold font-sans transition-all cursor-pointer ${
                      activeModalTab === "mcp"
                        ? "bg-[#111625] text-white shadow-sm"
                        : "text-slate-600 hover:bg-[#edf3f9]/50 hover:text-slate-900"
                    }`}
                  >
                    <Globe className="w-4 h-4 stroke-[2.5]" />
                    <span>MCP Connectors</span>
                  </button>

                  <button
                    onClick={() => setActiveModalTab("integrations")}
                    className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-[13px] font-bold font-sans transition-all cursor-pointer ${
                      activeModalTab === "integrations"
                        ? "bg-[#111625] text-white shadow-sm"
                        : "text-slate-600 hover:bg-[#edf3f9]/50 hover:text-slate-900"
                    }`}
                  >
                    <Cpu className="w-4 h-4 stroke-[2.5]" />
                    <span>Integrations</span>
                  </button>

                  <button
                    onClick={() => setActiveModalTab("preferences")}
                    className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-[13px] font-bold font-sans transition-all cursor-pointer ${
                      activeModalTab === "preferences"
                        ? "bg-[#111625] text-white shadow-sm"
                        : "text-slate-600 hover:bg-[#edf3f9]/50 hover:text-slate-900"
                    }`}
                  >
                    <Settings className="w-4 h-4 stroke-[2.5]" />
                    <span>Preferences</span>
                  </button>
                </div>
              </div>

              {/* Status and helpful footer inside sidebar */}
              <div className="px-1.5 py-2.5 bg-slate-100/50 rounded-xl border border-slate-200/40 text-[10.5px] text-slate-400 font-sans">
                <span className="font-extrabold text-slate-500 block mb-0.5 uppercase tracking-wide">Client Active</span>
                All protocol bridges run safely behind sandboxed server route proxies.
              </div>
            </div>

            {/* Right Display Area */}
            <div className="flex-1 bg-white p-6.5 flex flex-col overflow-hidden">
              {activeModalTab === "mcp" && (
                <div className="flex-1 flex flex-col overflow-hidden justify-between">
                  {/* Select Provider Segment */}
                  <div className="mb-3 shrink-0">
                    <span className="text-[11px] font-extrabold text-slate-400 font-sans uppercase tracking-widest block">
                      Select Provider
                    </span>
                  </div>

                  {/* Grid of Providers - Scrollable */}
                  <div className="flex-1 overflow-y-auto pr-1 pb-4 no-scrollbar grid grid-cols-2 gap-3.5">
                    {/*** Dynamic mapping of servers matching mockups ***/}
                    {servers.map((srv) => {
                      const isSrvSelected = srv.id === selectedServer.id;
                      const cleanedServerName = srv.name.replace(" MCP", "");
                      
                      return (
                        <div
                          key={srv.id}
                          onClick={() => handleSelectServer(srv)}
                          className={`rounded-xl p-4 transition-all duration-200 cursor-pointer text-left relative flex flex-col justify-between h-[95px] border ${
                            isSrvSelected
                              ? "border-[2px] border-[#111625] bg-white shadow-sm"
                              : "border-slate-200 bg-white hover:bg-slate-50/40 hover:border-slate-300/80"
                          }`}
                        >
                          {/* Inner Row: Name and connection badge */}
                          <div className="flex items-start justify-between">
                            <span className="font-extrabold text-[13.5px] font-display text-slate-905 tracking-tight leading-none mt-0.5">
                              {srv.name}
                            </span>

                            {srv.connected ? (
                              <div className="flex items-center gap-1 text-[#10b981] font-extrabold text-[9px] uppercase tracking-wider font-sans">
                                <span>✔</span>
                                <span>CONNECTED</span>
                              </div>
                            ) : srv.id === "zepto" ? (
                              <span className="text-[#3b82f6] text-[8.5px] font-extrabold uppercase font-sans tracking-wide">
                                SETUP REQUIRED
                              </span>
                            ) : (
                              <span className="text-slate-400 text-[8.5px] font-semibold uppercase font-sans tracking-wide">
                                READY
                              </span>
                            )}
                          </div>

                          {/* Bottom Row showing active state */}
                          <div className="text-[9.5px] font-extrabold font-mono text-slate-400 uppercase tracking-widest mt-auto select-none">
                            {srv.connected ? "Linked" : "Ready"}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {/* Bottom details status card */}
                  <div className="rounded-2xl bg-[#f8fafc] border border-slate-200/80 p-5 mt-auto flex flex-col gap-3.5 shrink-0">
                    <div className="flex items-center justify-between">
                      {/* Connection status tag */}
                      <div className="flex items-center gap-2">
                        <span className="text-[13px] font-bold text-slate-805">Status:</span>
                        {selectedServer.connected ? (
                          <div className="flex items-center gap-1.5 text-[#10b981] font-bold text-[12.5px] font-sans tracking-wide uppercase">
                            <span>✔</span>
                            <span>CONNECTED</span>
                          </div>
                        ) : (
                          <div className="flex items-center gap-1.5 text-slate-500 font-bold text-[12.5px] font-sans tracking-wide uppercase">
                            <div className="w-1.5 h-1.5 rounded-full bg-slate-400" />
                            <span>READY</span>
                          </div>
                        )}
                      </div>

                      {/* Sync Rotate button with simulated spin */}
                      <button
                        onClick={() => {
                          setIsRotating(true);
                          appLog("info", `Initiating protocol synchronization checks for ${selectedServer.name}...`);
                          setTimeout(() => {
                            setIsRotating(false);
                            appLog("success", `Security certification validation checklist passed on ${selectedServer.name.replace(" MCP", "")}`);
                          }, 850);
                        }}
                        className="p-1.5 hover:bg-slate-200/60 rounded-lg text-slate-400 hover:text-slate-700 transition-colors cursor-pointer"
                        title="Synchronize state keys"
                      >
                        <RotateCw className={`w-3.5 h-3.5 ${isRotating ? "animate-spin text-indigo-650" : ""}`} />
                      </button>
                    </div>

                    {/* Captions */}
                    <p className="text-[12px] text-slate-500 leading-snug font-sans">
                      {selectedServer.connected
                        ? "OAuth tokens are present in the mcp-remote token store."
                        : "Programmatic proxy pathways ready. Link connection keys to register with cognitive chat session."}
                    </p>

                    {/* Reconnect / Disconnect Button triggers */}
                    <div className="flex items-center gap-3">
                      <button
                        onClick={() => {
                          handleToggleServer(selectedServer.id);
                          appLog("success", `Triggered connection logic on ${selectedServer.name}`);
                        }}
                        className="flex-grow bg-[#111625] hover:bg-[#1a2135] active:scale-[0.99] text-white font-extrabold text-[12.5px] py-3 rounded-xl transition-all cursor-pointer text-center"
                      >
                        {selectedServer.connected ? "Reconnect" : "Connect"}
                      </button>
                      
                      {selectedServer.connected && (
                        <button
                          onClick={() => {
                            handleToggleServer(selectedServer.id);
                            appLog("info", `Cleanly disconnected from ${selectedServer.name}`);
                          }}
                          className="flex items-center justify-center gap-1.5 border border-[#fcb5b5]/50 hover:border-red-300 hover:bg-red-50 text-red-600 font-extrabold text-[12.5px] py-3 px-5 rounded-xl transition-all active:scale-[0.99] cursor-pointer"
                        >
                          <Unplug className="w-3.5 h-3.5 text-red-500 stroke-[2.2]" />
                          <span>Disconnect</span>
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {activeModalTab === "integrations" && (
                <div className="flex-1 flex flex-col justify-between">
                  <div className="space-y-4">
                    <div>
                      <span className="text-[11px] font-extrabold text-slate-400 font-sans uppercase tracking-widest block mb-1">
                        Active Sandbox Integrations
                      </span>
                      <p className="text-[12px] text-slate-505 dark:text-slate-400">
                        Authorized API pipelines linking standard ecosystem modules for interactive map actions or schedules.
                      </p>
                    </div>

                    <div className="grid grid-cols-2 gap-3.5 pt-1">
                      {/* Integration Box 1: Google Calendar */}
                      <div className="p-4 rounded-2xl border border-slate-200 bg-slate-50/50 flex flex-col justify-between h-[120px]">
                        <div>
                          <span className="font-extrabold text-[13.5px] text-slate-800 block">Google Calendar</span>
                          <span className="text-[10.5px] text-slate-400 leading-snug mt-1.5 block">Exposes local agendas and timeline markers.</span>
                        </div>
                        <button
                          onClick={handleCalendarAction}
                          className="text-[11px] font-bold text-[#111625] hover:text-indigo-800 flex items-center gap-1 select-none cursor-pointer"
                        >
                          <span>Synchronize Calendar</span>
                          <span>➜</span>
                        </button>
                      </div>

                      {/* Integration Box 2: Google Maps */}
                      <div className="p-4 rounded-2xl border border-slate-200 bg-slate-50/50 flex flex-col justify-between h-[120px]">
                        <div>
                          <span className="font-extrabold text-[13.5px] text-slate-800 block">Google Maps Platform</span>
                          <span className="text-[10.5px] text-slate-400 leading-snug mt-1.5 block">Fetches delivery estimates, coordinates, and routes.</span>
                        </div>
                        <button
                          onClick={handleMapAction}
                          className="text-[11px] font-bold text-[#111625] hover:text-indigo-800 flex items-center gap-1 select-none cursor-pointer"
                        >
                          <span>Re-center coordinates</span>
                          <span>➜</span>
                        </button>
                      </div>
                    </div>
                  </div>

                  <div className="p-3.5 rounded-xl bg-orange-50 border border-orange-100 flex items-start gap-2.5">
                    <AlertTriangle className="w-4 h-4 text-orange-500 shrink-0 mt-0.5" />
                    <p className="text-[11px] text-orange-800 leading-relaxed font-sans">
                      <strong>Sandbox Notice:</strong> Connections are fully sandboxed. Token databases reside offline inside memory caches. All actions are completely safe and secure.
                    </p>
                  </div>
                </div>
              )}

              {activeModalTab === "preferences" && (
                <div className="flex-1 flex flex-col justify-between animate-fade-in">
                  <div className="space-y-4">
                    <div>
                      <span className="text-[11px] font-extrabold text-slate-400 font-sans uppercase tracking-widest block mb-1">
                        Companion Preferences
                      </span>
                      <p className="text-[12px] text-slate-505">
                        Customize visual delays, telemetry details, and model behaviors.
                      </p>
                    </div>

                    <div className="space-y-3">
                      <div className="flex items-center justify-between p-3.5 border border-slate-200 rounded-xl bg-slate-50/30">
                        <div>
                          <span className="font-extrabold text-slate-800 text-[12.5px] block">Simulate Cognitive Streaming Delay</span>
                          <span className="text-[10.5px] text-slate-400 block mt-0.5">Applies slight synthetic latency for realistic validation processing.</span>
                        </div>
                        <span className="px-2.5 py-1 text-[9.5px] font-extrabold bg-[#10b981] text-white rounded-md uppercase">Active</span>
                      </div>

                      <div className="flex items-center justify-between p-3.5 border border-slate-200 rounded-xl bg-slate-50/30">
                        <div>
                          <span className="font-extrabold text-slate-800 text-[12.5px] block">Automatic History Syncing</span>
                          <span className="text-[10.5px] text-slate-400 block mt-0.5 font-sans">Stores context elements locally to speed up initial launch sequences.</span>
                        </div>
                        <span className="px-2.5 py-1 text-[9.5px] font-extrabold bg-[#10b981] text-white rounded-md uppercase font-sans">Enabled</span>
                      </div>
                    </div>
                  </div>

                  <div className="p-3.5 rounded-2xl bg-slate-100/60 border border-slate-200/50 flex items-center justify-between">
                    <div>
                      <span className="font-bold text-slate-750 text-xs block">AI Model Channel</span>
                      <span className="text-slate-400 text-[10.5px]">Running on Authorized model models/gemini-3.5-flash</span>
                    </div>
                    <span className="text-[9px] font-mono font-bold bg-[#111625] text-white px-2 py-0.5 rounded">V2.6 RELEASE</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Notification Toast Dialog Overlay */}
      {showNotification.visible && (
        <div className="absolute inset-0 z-100 flex items-center justify-center bg-transparent pointer-events-none p-4">
          <div className="bg-slate-900/95 text-slate-100 border border-slate-700/60 rounded-2xl max-w-sm p-4 shadow-2xl backdrop-blur-lg flex flex-col gap-3 pointer-events-auto select-none pointer-events-auto relative">
            <button
              onClick={() => setShowNotification({ ...showNotification, visible: false })}
              className="absolute top-3 right-3 text-slate-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>
            
            <div className="flex items-center gap-2">
              <Sparkles className="w-4.5 h-4.5 text-amber-400" />
              <span className="font-bold text-slate-50 text-[13.5px] uppercase tracking-wider">{showNotification.title}</span>
            </div>
            
            <p className="text-[12.5px] text-slate-300 leading-relaxed font-mono">
              {showNotification.message}
            </p>

            <button
              onClick={() => setShowNotification({ ...showNotification, visible: false })}
              className="mt-1 w-full bg-slate-800 hover:bg-slate-700 text-slate-100 font-semibold text-xs py-2 rounded-lg cursor-pointer transition-colors"
            >
              Acknowledge
            </button>
          </div>
        </div>
      )}

      {/* ========================================== */}
      {/* 🔮 REMINDERS DIALOG OVERLAY POPUP (CENTERED) */}
      {/* ========================================== */}
      <AnimatePresence>
        {showRemindersPopup && (
          <RemindersPopup
            isOpen={showRemindersPopup}
            onClose={() => setShowRemindersPopup(false)}
            reminders={reminders}
            onToggleReminder={handleToggleReminder}
            onAddReminder={handleAddReminder}
            onDeleteReminder={handleDeleteReminder}
            onClearCompleted={handleClearCompletedReminders}
          />
        )}
      </AnimatePresence>

      {/* ========================================== */}
      {/* 💼 ORDERS DIALOG OVERLAY POPUP (CENTERED) */}
      {/* ========================================== */}
      <AnimatePresence>
        {showOrdersPopup && (
          <OrdersPopup
            isOpen={showOrdersPopup}
            onClose={() => setShowOrdersPopup(false)}
            orders={orders}
            onCancelOrder={handleCancelOrder}
            onAddOrder={handleAddOrder}
            onDeleteOrder={handleDeleteOrder}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
