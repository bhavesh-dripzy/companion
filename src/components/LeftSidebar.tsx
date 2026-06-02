/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { 
  Plus, 
  Settings, 
  LogOut, 
  Clock, 
  Layers, 
  Sliders, 
  Calendar, 
  MapPin, 
  Info, 
  X,
  MessageSquare,
  Sparkles,
  ChevronRight,
  ChevronDown,
  FolderOpen,
  Home,
  Bell,
  Heart,
  ShoppingBag,
  Users,
  Sun,
  Search,
  CheckCircle2,
  Check,
  Trash2,
  Package,
  Truck
} from "lucide-react";
import { McpServer, ChatMessage } from "../types";

/// Historic Mock Thread items for display and interaction
const historicThreads = [
  {
    id: "hist-pizza",
    title: "find me some pizza options near me",
    preview: "Toit, Brik Oven, Pizza Bakery...",
    time: "10:24 AM",
    category: "Today",
    messages: [
      {
        id: "m-p1",
        sender: "user" as const,
        text: "find me some pizza options near me",
        timestamp: "10:24 AM"
      },
      {
        id: "m-p2",
        sender: "assistant" as const,
        text: `Active Model Context Protocol Tool: **Zomato Search Engine** 🍕

I found 3 exceptional high-rated sourdough wood-fired pizza spots close to your Indiranagar location:

1. **Toit Brewpub** (4.6 ⭐)
   * *Specialty*: Sourdough Pepperoni, Classic Margherita, and Craft Ales.
   * *Distance*: 1.1 km | *Deals*: 10% cash-back on reservation.
2. **Brik Oven** (4.5 ⭐)
   * *Specialty*: Neapolitan wood-fired pizzas, stuffed burrata crusts.
3. **The Pizza Bakery** (4.4 ⭐)
   * *Specialty*: Famous Caramelized Onion & Goat Cheese with stuffed garlic bread.

Would you like me to book a dynamic slot or query their active menu?`,
        timestamp: "10:25 AM",
        mcpCall: {
          serverName: "Zomato Server",
          toolName: "Search Pizza Options",
          arguments: { position: "Indiranagar, Bangalore", radius_km: 2 },
          status: "success" as const,
          result: "Found Toit, Brik Oven, and Pizza Bakery within 2km boundary rules."
        }
      }
    ]
  },
  {
    id: "hist-commute",
    title: "Medicine reminder for dad",
    preview: "Outer Ring Rd vs HAL Airport...",
    time: "Yesterday",
    category: "Yesterday",
    messages: [
      {
        id: "m-c1",
        sender: "user" as const,
        text: "remind me or book a cab for me to Whitefield",
        timestamp: "Yesterday 9:42 AM"
      },
      {
        id: "m-c2",
        sender: "assistant" as const,
        text: `Active Model Context Protocol Tool: **Google Maps Router** 🚗

I calculated real-time traffic density and predicted travel times:

* **Fastest Route** (via Outer Ring Rd): **38 minutes** (14.2 km). Standard moderate flow. Only 3 minutes delay near KR Puram.
* **Alternative Route** (via HAL Old Airport Road): **47 minutes**. Stagnant congestion warning at Marathahalli flyover border (adds +12 mins delay).

I can dispatch a standard **Ola/Uber cab simulation** for you or sync this travel constraint into your Google Calendar. Shall we book starting right away?`,
        timestamp: "Yesterday 9:45 AM",
        mcpCall: {
          serverName: "Google Maps Router",
          toolName: "Compute Indiranagar ETA",
          arguments: { origin: "Indiranagar Office", destination: "Whitefield Bangalore", traffic: "peak_traffic" },
          status: "success" as const,
          result: "Calculated ORR route at 38m, HAL Airport road at 47m."
        }
      }
    ]
  },
  {
    id: "hist-dineout",
    title: "Book doctor appointment",
    preview: "Sure! 😊 Please provide more de...",
    time: "Yesterday",
    category: "Yesterday",
    messages: [
      {
        id: "m-d1",
        sender: "user" as const,
        text: "Can you help me book doctor appointment?",
        timestamp: "Yesterday 7:54 PM"
      },
      {
        id: "m-d2",
        sender: "assistant" as const,
        text: "Sure! Based on standard calendar slots, I found available timings at Bangalore Orthopedic Clinic at 4:30 PM today.",
        timestamp: "Yesterday 7:55 PM"
      }
    ]
  },
  {
    id: "hist-instamart",
    title: "Order groceries",
    preview: "Akshayakalpa Organic, Country...",
    time: "Apr 24",
    category: "Previous 7 Days",
    messages: [
      {
        id: "m-i1",
        sender: "user" as const,
        text: "Is organic milk in stock on Swiggy Instamart?",
        timestamp: "Apr 24 4:00 PM"
      },
      {
        id: "m-i2",
        sender: "assistant" as const,
        text: `Active Model Context Protocol Tool: **Swiggy Instamart Inventory API** 🥛

I executed a live stock query against the Bangalore central logistics node:

* **Akshayakalpa Organic Whole Milk (1L)**: ✅ **IN STOCK** (12 units available in nearest warehouse).
* **Country Delight Buffalo Milk (500ml)**: ❌ **OUT OF STOCK** (Expect restocking in 8 hours).

I can place a cart reserve command for 2 packs of Akshayakalpa Whole Milk. Would you like me to checkout?`,
        timestamp: "Apr 24 4:02 PM",
        mcpCall: {
          serverName: "Swiggy Instamart",
          toolName: "Check Grocery Stock",
          arguments: { item: "Organic Whole Milk", location: "Bangalore Central" },
          status: "success" as const,
          result: "In stock: Akshayakalpa Organic Whole Milk (12 units). Out of stock: Country Delight."
        }
      }
    ]
  },
  {
    id: "hist-jobinterview",
    title: "Weekend plan for family",
    preview: "Preparing strategic responses to direct...",
    time: "Apr 23",
    category: "Previous 7 Days",
    messages: [
      {
        id: "m-j1",
        sender: "user" as const,
        text: "Help me make a family weekend plan",
        timestamp: "Apr 23 11:15 AM"
      },
      {
        id: "m-j2",
        sender: "assistant" as const,
        text: "I compiled some weekend activities: Visit Bannerghatta national park, family board games evening, and dining at a local cafe.",
        timestamp: "Apr 23 11:16 AM"
      }
    ]
  }
];

interface LeftSidebarProps {
  servers: McpServer[];
  onToggleServer: (id: string) => void;
  onSelectServer: (server: McpServer | null) => void;
  selectedServer: McpServer | null;
  onTriggerMapAction: () => void;
  onTriggerCalendarAction: () => void;
  onAddStream?: () => void;
  openSettings?: () => void;
  onExplore?: () => void;
  onClearMessages?: () => void;
  onSelectThread?: (messages: ChatMessage[], title: string) => void;
  sysStatus?: any;
  isSidebarCollapsed?: boolean;
  onToggleCollapse?: () => void;
  onShowReminders?: () => void;
  onShowOrders?: () => void;
}

const getFriendlyToolDetails = (toolName: string) => {
  const mapping: Record<string, { title: string; description: string; example: string }> = {
    // Zomato
    zomato_search_restaurants: {
      title: "Find Nearby Dining",
      description: "Search for restaurant menus, locations, and user reviews based on your flavor preferences.",
      example: "Recommend some rated sushi spots around Indiranagar"
    },
    zomato_get_restaurant_menu: {
      title: "Explore Restaurant Menus",
      description: "Instantly retrieve real-time menus, prices, and special dishes from any listed restaurant.",
      example: "Show me the menu for Toit Brewpub"
    },
    zomato_place_order: {
      title: "Direct Food Ordering",
      description: "Order selected items directly to your address with automatic promotion matching.",
      example: "Order 2 cheese pizzas from Brik Oven"
    },
    zomato_get_order_status: {
      title: "Track Food Orders",
      description: "Monitor real-time live order delivery status, rider information, and arrival ETA.",
      example: "Check my active Zomato order status"
    },
    // Swiggy Food
    swiggy_search_food: {
      title: "Cuisine Search",
      description: "Find specific dishes, meal preferences, and custom cuisine platters on Swiggy.",
      example: "Find butter chicken available right now"
    },
    swiggy_get_offers: {
      title: "Find Promo Code Deals",
      description: "Discover flat discounts, seasonal coupons, and limited offers to slash delivery prices.",
      example: "Show me active Swiggy coupons"
    },
    // Swiggy Instamart
    instamart_quick_search: {
      title: "10-Min Grocery Check",
      description: "Scan local dark stores for immediate availability of daily household essentials and snacks.",
      example: "Do you have Country Delight milk in stock?"
    },
    // District
    district_get_events: {
      title: "Community Events Hub",
      description: "Scan upcoming weekend markets, citizen clean-up drives, and community festivals near you.",
      example: "What events are happening in Indiranagar today?"
    },
    // Zepto
    zepto_check_stock: {
      title: "Flash Grocery Check",
      description: "Inspect live warehouse inventory for immediate 10-minute grocery shipments.",
      example: "Is organic salted butter in stock on Zepto?"
    },
    // Zerodha Kite
    kite_get_live_price: {
      title: "Real-time Stock Pricing",
      description: "Retrieve live trading quotes, percent changes, and volumes on the NSE index.",
      example: "What is the live price of HDFC Bank?"
    },
    kite_buy_shares: {
      title: "Order Placement Interface",
      description: "Programmatically trigger buy or sell orders matching custom volume limits safely.",
      example: "Buy 10 shares of Reliance at market price"
    },
    // Groww
    groww_sip_calculator: {
      title: "SIP Returns Projection",
      description: "Estimate compound growth outcomes for mutual funds across custom year intervals.",
      example: "Estimate returns on 5000/month SIP for 10 years"
    },
    // Daily Calorie
    tracker_log_meal: {
      title: "Log Daily Nutrition",
      description: "Identify food calories, macros, proteins, and tracker goals for healthy wellness habits.",
      example: "Log a meal of scrambled eggs and avocado toast"
    }
  };

  return mapping[toolName] || {
    title: toolName.split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" "),
    description: "Connects this companion with standard actions.",
    example: "Ask about this action in chat"
  };
};

export function LeftSidebar({
  servers,
  onToggleServer,
  onSelectServer,
  selectedServer,
  onTriggerMapAction,
  onTriggerCalendarAction,
  onAddStream,
  openSettings,
  onExplore,
  onClearMessages,
  onSelectThread,
  sysStatus,
  isSidebarCollapsed,
  onToggleCollapse,
  onShowReminders,
  onShowOrders
}: LeftSidebarProps) {
  const [time, setTime] = useState("");
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>("hist-pizza");
  const [activeTab, setActiveTab] = useState("reminders");
  
  // Accordion state matching the design
  const [collapsedCategories, setCollapsedCategories] = useState<Record<string, boolean>>({
    "Today": false,
    "Yesterday": false,
    "Previous 7 Days": false
  });

  const connectedCount = servers.filter((s) => s.connected).length;
  const totalCount = servers.length;

  useEffect(() => {
    const updateClock = () => {
      const now = new Date();
      const hrs = String(now.getHours()).padStart(2, "0");
      const mins = String(now.getMinutes()).padStart(2, "0");
      setTime(`${hrs}:${mins}`);
    };

    updateClock();
    const interval = setInterval(updateClock, 1000);
    return () => clearInterval(interval);
  }, []);

  const handleThreadSelect = (thread: typeof historicThreads[number]) => {
    setSelectedThreadId(thread.id);
    if (onSelectThread) {
      onSelectThread(thread.messages, thread.title);
    }
  };

  const toggleCategory = (category: string) => {
    setCollapsedCategories(prev => ({
      ...prev,
      [category]: !prev[category]
    }));
  };

  // Main navigation items - keeping Reminders, Health, Orders, and Family
  const navigationPages = [
    { id: "reminders", label: "Reminders", icon: Bell, badge: 8, bg: "bg-purple-50 text-purple-600 border-purple-100" },
    { id: "health", label: "Health", icon: Heart, bg: "bg-rose-50 text-rose-500 border-rose-100" },
    { id: "orders", label: "Orders", icon: ShoppingBag, bg: "bg-amber-50 text-amber-600 border-amber-100" },
    { id: "family", label: "Family", icon: Users, bg: "bg-indigo-50 text-indigo-600 border-indigo-100" },
  ];

  return (
    <div className="w-80 bg-gradient-to-b from-[#f8fafc] via-[#edf3f9] to-[#f4f7fc] border-r border-[#d5e4f3]/80 flex flex-col h-full text-slate-750 relative select-none shrink-0 transition-all duration-300">
      
      {/* 🚀 Overhauled Top Navigation / Header strictly inspired by the design */}
      <div className="py-2.5 px-4 flex items-center justify-between shrink-0 border-b border-slate-200/50">
        <div className="flex items-center gap-2.5">
          {/* Black Orb Logo representing Companion */}
          <div className="w-8.5 h-8.5 rounded-full bg-slate-900 flex items-center justify-center relative shadow-sm shrink-0">
            <div className="w-4 h-4 rounded-full border border-white/20 absolute animate-spin-slow" />
            <div className="w-1.5 h-1.5 rounded-full bg-indigo-400 absolute animate-pulse" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-extrabold text-[15.5px] font-display text-slate-900 tracking-tight leading-none">
                Companion
              </span>
              <span className="bg-indigo-50/80 text-indigo-650 px-1.5 py-0.5 rounded text-[8.5px] font-extrabold font-mono tracking-tight leading-none border border-indigo-100/60">
                v2.6
              </span>
            </div>
            <span className="text-[10.5px] text-slate-400 block font-medium mt-0.5 font-sans leading-none">
              AI that cares
            </span>
          </div>
        </div>

        {/* Sidebar Toggle layout pane Button positioned perfectly at the right corner */}
        {onToggleCollapse && (
          <button
            onClick={onToggleCollapse}
            className="p-1.5 hover:bg-white border border-[#e3e1e9]/20 text-slate-500 hover:text-slate-800 rounded-lg transition-all cursor-pointer flex items-center justify-center shadow-3xs"
            title="Collapse sidebar"
            id="collapse-sidebar-btn"
          >
            <svg 
              className="w-3.5 h-3.5 text-slate-500"
              fill="none" 
              stroke="currentColor" 
              strokeWidth="2.5" 
              viewBox="0 0 24 24"
            >
              <rect x="3" y="3" width="18" height="18" rx="3.5" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 3v18" />
            </svg>
          </button>
        )}
      </div>

      {/* Sidebar Main Scrollable Area - halved the padding and margins */}
      <div className="flex-1 overflow-y-auto px-3.5 pb-3.5 pt-3 space-y-2.5 no-scrollbar">

        {/* + New Chat Pill Button */}
        <div className="px-0.5">
          <button
            onClick={onClearMessages}
            className="w-full bg-gradient-to-r from-slate-900 to-slate-800 hover:from-slate-950 hover:to-slate-850 text-white font-bold text-[13.5px] py-2.5 px-3.5 rounded-xl flex items-center justify-between transition-all active:scale-[0.98] cursor-pointer shadow-[0_1.5px_3px_rgba(15,23,42,0.06)] group font-display border border-slate-950/20"
          >
            <div className="flex items-center gap-1.5">
              <Plus className="w-3.5 h-3.5 text-white stroke-[2.5]" />
              <span>New Chat</span>
            </div>
            <span className="text-[10px] text-slate-300 px-1.5 py-0.5 bg-white/10 rounded font-mono font-semibold tracking-tight uppercase">
              new
            </span>
          </button>
        </div>

        {/* 🧭 Vertical tab navigation matching mockup with elegant category badges */}
        <nav className="space-y-0.5">
          {navigationPages.map((page) => {
            const IconComponent = page.icon;
            const isTabActive = activeTab === page.id;
            return (
              <button
                key={page.id}
                onClick={() => {
                  setActiveTab(page.id);
                  if (page.id === "reminders") {
                    onShowReminders?.();
                  } else if (page.id === "orders") {
                    onShowOrders?.();
                  } else if (page.id === "home") {
                    onClearMessages?.();
                  }
                }}
                className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg transition-all duration-150 font-medium text-[13.5px] font-sans text-left cursor-pointer group ${
                  isTabActive
                    ? "bg-white text-slate-900 font-bold border border-slate-200/60 shadow-[0_1.5px_3px_rgba(0,0,0,0.01)]"
                    : "text-slate-600 hover:bg-white/60 hover:text-slate-900"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <div className={`p-1 rounded-md border transition-all ${
                    isTabActive ? page.bg : "bg-white border-slate-100 group-hover:bg-slate-50 text-slate-500"
                  }`}>
                    <IconComponent className="w-3.5 h-3.5 stroke-[2.2]" />
                  </div>
                  <span className={isTabActive ? "font-bold text-slate-900" : "font-medium text-slate-600"}>{page.label}</span>
                </div>
                {page.badge ? (
                  <span className="text-[10px] font-extrabold text-white bg-indigo-500 px-1.5 rounded-full font-mono">
                    {page.badge}
                  </span>
                ) : (
                  <ChevronRight className={`w-3 h-3 transition-all text-slate-300 group-hover:text-slate-500 ${
                    isTabActive ? "opacity-100 translate-x-0.5" : "opacity-0 -translate-x-1"
                  }`} />
                )}
              </button>
            );
          })}
        </nav>

        {/* 💬 Beautiful Chronological Conversation list with Search Glass */}
        <div className="space-y-2 pt-1">
          <div className="flex items-center justify-between px-1 pb-0.5">
            <span className="text-[11px] font-extrabold text-slate-400 font-display uppercase tracking-wider">
              Recent Conversations
            </span>
            <Search className="w-3.5 h-3.5 text-slate-400 cursor-pointer hover:text-slate-800 transition-colors" />
          </div>
          
          {/* Today Block Group */}
          <div className="space-y-0.5">
            <div className="text-[9.5px] font-bold text-slate-400/70 uppercase tracking-widest px-2 pb-0.5 select-none">
              Today
            </div>
            {historicThreads.filter((t) => t.category === "Today").map((thread) => {
              const isActive = selectedThreadId === thread.id;
              return (
                <div
                  key={thread.id}
                  onClick={() => handleThreadSelect(thread)}
                  className={`group relative flex items-center justify-between py-1.5 px-2 rounded-lg border transition-all duration-200 cursor-pointer ${
                    isActive
                      ? "bg-white border-indigo-200/80 shadow-[0_1.5px_3px_rgba(0,0,0,0.01)] font-semibold text-slate-900"
                      : "bg-transparent border-transparent hover:bg-white/40 text-slate-600 hover:text-slate-900"
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <MessageSquare className={`w-3.5 h-3.5 shrink-0 transition-colors ${
                      isActive ? "text-indigo-500" : "text-slate-400/70"
                    }`} />
                    <span className="text-[12.5px] font-bold truncate">
                      {thread.title}
                    </span>
                  </div>
                  <span className="text-[10px] font-mono text-slate-400 shrink-0 select-none pl-1.5 group-hover:text-slate-500">
                    {thread.time}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Yesterday Block Group */}
          <div className="space-y-0.5">
            <div className="text-[9.5px] font-bold text-slate-400/70 uppercase tracking-widest px-2 pb-0.5 select-none">
              Yesterday
            </div>
            {historicThreads.filter((t) => t.category === "Yesterday").map((thread) => {
              const isActive = selectedThreadId === thread.id;
              return (
                <div
                  key={thread.id}
                  onClick={() => handleThreadSelect(thread)}
                  className={`group relative flex items-center justify-between py-1.5 px-2 rounded-lg border transition-all duration-200 cursor-pointer ${
                    isActive
                      ? "bg-white border-indigo-200/80 shadow-[0_1.5px_3px_rgba(0,0,0,0.01)] font-semibold text-slate-900"
                      : "bg-transparent border-transparent hover:bg-white/40 text-slate-600 hover:text-slate-900"
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <MessageSquare className={`w-3.5 h-3.5 shrink-0 transition-colors ${
                      isActive ? "text-indigo-500" : "text-slate-400/70"
                    }`} />
                    <span className="text-[12.5px] font-bold truncate">
                      {thread.title}
                    </span>
                  </div>
                  <span className="text-[10px] font-mono text-slate-400 shrink-0 select-none pl-1.5 group-hover:text-slate-500">
                    Yesterday
                  </span>
                </div>
              );
            })}
          </div>

          {/* Previous 7 Days Block Group */}
          <div className="space-y-0.5">
            <div className="text-[9.5px] font-bold text-slate-400/70 uppercase tracking-widest px-2 pb-0.5 select-none">
              Past Week
            </div>
            {historicThreads.filter((t) => t.category === "Previous 7 Days").map((thread) => {
              const isActive = selectedThreadId === thread.id;
              return (
                <div
                  key={thread.id}
                  onClick={() => handleThreadSelect(thread)}
                  className={`group relative flex items-center justify-between py-1.5 px-2 rounded-lg border transition-all duration-200 cursor-pointer ${
                    isActive
                      ? "bg-white border-indigo-200/80 shadow-[0_1.5px_3px_rgba(0,0,0,0.01)] font-semibold text-slate-900"
                      : "bg-transparent border-transparent hover:bg-white/40 text-slate-600 hover:text-slate-900"
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <MessageSquare className={`w-3.5 h-3.5 shrink-0 transition-colors ${
                      isActive ? "text-indigo-500" : "text-slate-400/70"
                    }`} />
                    <span className="text-[12.5px] font-bold truncate">
                      {thread.title}
                    </span>
                  </div>
                  <span className="text-[10px] font-mono text-slate-400 shrink-0 select-none pl-1.5 group-hover:text-slate-500">
                    {thread.time}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Connected Apps Grid Section added beautifully */}
        <div className="space-y-2 pt-3.5 border-t border-slate-200/50">
          <div className="flex items-center justify-between px-1 pb-0.5">
            <span className="text-[11px] font-extrabold text-slate-400 font-display uppercase tracking-wider">
              Connected Apps
            </span>
            <span className="text-[9.5px] font-mono font-bold text-slate-500 bg-slate-200/50 px-1.5 py-0.5 rounded-full select-none">
              {connectedCount}/{totalCount} Active
            </span>
          </div>

          <div className="grid grid-cols-2 gap-2">
            {servers.map((server) => {
              const cleanedName = server.name.replace(" MCP", "");
              return (
                <div
                  key={server.id}
                  onClick={() => onSelectServer(server)}
                  className="group relative flex items-center gap-2 p-2 rounded-lg bg-white/60 hover:bg-white border border-slate-200/40 hover:border-slate-300/80 transition-all duration-200 cursor-pointer shadow-3xs active:scale-[0.98]"
                  title={`Click to view ${cleanedName} details`}
                >
                  {/* Compact App Icon */}
                  <div className={`w-6.5 h-6.5 rounded-md flex items-center justify-center font-bold text-[11px] shrink-0 ${
                    server.connected ? `${server.iconBgColor} ${server.iconTextColor}` : "bg-slate-200 text-slate-400"
                  }`}>
                    {server.iconText}
                  </div>

                  {/* App Description & Status */}
                  <div className="min-w-0 flex-1 flex flex-col justify-center">
                    <span className="text-[11.5px] font-bold text-slate-800 truncate group-hover:text-indigo-650 transition-colors">
                      {cleanedName}
                    </span>
                    <div className="flex items-center gap-1">
                      <span className={`w-1.5 h-1.5 rounded-full ${
                        server.connected ? "bg-emerald-500 animate-pulse" : "bg-slate-300"
                      }`} />
                      <span className="text-[8.5px] font-medium text-slate-405 select-none">
                        {server.connected ? "Connected" : "Offline"}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

    </div>
  );
}
