import React, { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { ShoppingBag, X, Clock, HelpCircle, AlertTriangle, Plus, Search, Trash2, CheckCircle, Smartphone } from "lucide-react";

interface Order {
  id: string;
  category: string; // "daily" | "today"
  provider: string; // e.g., "Swiggy Daily", "Zepto"
  text: string;
  status: string; // "Active Subscription" | "Paused" | "In Transit" | "Preparing" | "Delivered" | "Cancelled"
  time: string;
  price: string;
}

interface OrdersPopupProps {
  isOpen: boolean;
  onClose: () => void;
  orders: Order[];
  onCancelOrder: (id: string) => void;
  onAddOrder: (provider: string, text: string, category: "daily" | "today", price: string) => void;
  onDeleteOrder?: (id: string) => void;
}

const getStatusColor = (status: string) => {
  switch (status.toLowerCase()) {
    case "delivered":
      return "bg-emerald-50 text-emerald-700 border-emerald-100/80";
    case "in transit":
      return "bg-sky-50 text-sky-700 border-sky-105/80 animate-pulse";
    case "preparing":
      return "bg-amber-50 text-amber-700 border-amber-105/80";
    case "active subscription":
      return "bg-purple-50 text-purple-700 border-purple-105/80";
    case "paused":
      return "bg-slate-100 text-slate-500 border-slate-200";
    case "cancelled":
      return "bg-red-50 text-red-550 border-red-105/80";
    default:
      return "bg-slate-50 text-slate-600 border-slate-200";
  }
};

const getProgressBarWidth = (status: string) => {
  switch (status.toLowerCase()) {
    case "delivered":
      return "w-full bg-emerald-500";
    case "in transit":
      return "w-3/4 bg-sky-500";
    case "preparing":
      return "w-1/3 bg-amber-500";
    case "active subscription":
      return "w-full bg-purple-500";
    case "paused":
      return "w-0 bg-slate-300";
    case "cancelled":
      return "w-full bg-red-400";
    default:
      return "w-0 bg-slate-200";
  }
};

export function OrdersPopup({
  isOpen,
  onClose,
  orders,
  onCancelOrder,
  onAddOrder,
  onDeleteOrder,
}: OrdersPopupProps) {
  const [newProvider, setNewProvider] = useState("");
  const [newText, setNewText] = useState("");
  const [category, setCategory] = useState<"daily" | "today">("today");
  const [price, setPrice] = useState("₹150");
  const [searchQuery, setSearchQuery] = useState("");
  const [filterProvider, setFilterProvider] = useState("all");

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newText.trim() || !newProvider.trim()) return;
    onAddOrder(newProvider.trim(), newText.trim(), category, price.startsWith("₹") ? price : `₹${price}`);
    setNewProvider("");
    setNewText("");
    setPrice("₹150");
  };

  // Extract unique providers for quick tabs
  const providers = ["all", ...Array.from(new Set(orders.map((o) => o.provider.split(" ")[0].toLowerCase())))];

  const filteredOrders = orders.filter((o) => {
    const matchesSearch =
      o.text.toLowerCase().includes(searchQuery.toLowerCase()) ||
      o.provider.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesProvider =
      filterProvider === "all" || o.provider.toLowerCase().startsWith(filterProvider.toLowerCase());
    return matchesSearch && matchesProvider;
  });

  const dailyCount = orders.filter((o) => o.category === "daily" && o.status !== "Cancelled").length;
  const todayCount = orders.filter((o) => o.category === "today" && o.status !== "Cancelled" && o.status !== "Delivered").length;

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4">
      {/* Intense dark backdrop blur */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
        className="absolute inset-0 bg-slate-950/70 backdrop-blur-md"
      />

      {/* Floating Center Card container */}
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 15 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 15 }}
        transition={{ type: "spring", duration: 0.4, bounce: 0.15 }}
        className="relative bg-white border border-slate-100 rounded-3xl w-full max-w-2xl shadow-[0_30px_70px_-15px_rgba(15,23,42,0.22)] flex flex-col max-h-[88vh] overflow-hidden text-slate-800"
      >
        {/* Subtle orange-amber upper glow line */}
        <div className="absolute top-0 inset-x-0 h-[3px] bg-gradient-to-r from-amber-500 via-orange-500 to-rose-500" />

        {/* Modal Header */}
        <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-gradient-to-b from-slate-50/50 to-white">
          <div className="flex items-center gap-3.5">
            <div className="w-11 h-11 rounded-2xl bg-amber-50 text-amber-600 border border-amber-100 flex items-center justify-center shadow-xs">
              <ShoppingBag className="w-5.5 h-5.5 stroke-[2.2] animate-pulse" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-lg font-extrabold text-slate-900 tracking-tight font-sans">
                  Companion Orders Monitor
                </h3>
                <span className="bg-amber-100/70 text-amber-800 text-[10px] font-bold px-2 py-0.5 rounded-full flex items-center gap-1">
                  <Smartphone className="w-3.5 h-3.5" /> Dispatch Stream
                </span>
              </div>
              <p className="text-xs text-slate-400 font-medium tracking-wide mt-0.5">
                Monitoring instant delivery portals and subscription orders in real time.
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-9 h-9 rounded-full bg-slate-100/60 hover:bg-slate-150 border border-slate-200/50 text-slate-450 hover:text-slate-705 flex items-center justify-center transition-all cursor-pointer"
          >
            <X className="w-4.5 h-4.5 stroke-[2.5]" />
          </button>
        </div>

        {/* Search & Provider Filter Header */}
        <div className="px-6 py-3.5 bg-slate-50/40 border-b border-slate-100 flex flex-col sm:flex-row gap-3 items-center justify-between">
          <div className="flex items-center gap-1.5 bg-slate-100/90 p-1 rounded-xl w-full sm:w-auto overflow-x-auto scrollbar-none">
            {providers.slice(0, 5).map((prov) => (
              <button
                key={prov}
                onClick={() => setFilterProvider(prov)}
                className={`flex-none capitalize text-xs font-bold px-3.5 py-1.5 rounded-lg transition-all duration-150 cursor-pointer ${
                  filterProvider === prov
                    ? "bg-white text-slate-900 shadow-3xs"
                    : "text-slate-500 hover:text-slate-800"
                }`}
              >
                {prov}
              </button>
            ))}
          </div>

          <div className="relative w-full sm:w-60 shrink-0">
            <Search className="absolute left-3 top-2.5 w-4 h-4 text-slate-450 stroke-[2]" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search providers or items..."
              className="w-full bg-slate-100/80 border border-slate-200/40 focus:border-amber-300 rounded-xl pl-9 pr-4 py-2 text-xs text-slate-800 placeholder:text-slate-450 focus:outline-none transition-all"
            />
          </div>
        </div>

        {/* Modal Scrollable Workspace */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6 scrollbar-thin scrollbar-thumb-slate-300 scrollbar-track-transparent">
          {/* Two-Column responsive distribution */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Column A: Subscriptions */}
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-2 border-b border-amber-100/60">
                <span className="text-[11.5px] font-extrabold text-amber-700 tracking-wider uppercase font-sans flex items-center gap-1.5">
                  🥛 Daily Subscriptions
                </span>
                <span className="bg-amber-50 text-amber-700 px-2 py-0.5 rounded-full text-[9.5px] font-bold font-mono border border-amber-100">
                  {dailyCount} Active
                </span>
              </div>

              <div className="space-y-3">
                {filteredOrders.filter((o) => o.category === "daily").length === 0 ? (
                  <div className="text-center py-6 px-4 bg-slate-50/50 border border-dashed border-slate-150 rounded-2xl">
                    <p className="text-[11.5px] font-medium text-slate-400">No active subscriptions configured</p>
                  </div>
                ) : (
                  filteredOrders
                    .filter((o) => o.category === "daily")
                    .map((ord) => (
                      <div
                        key={ord.id}
                        className={`p-4 rounded-2xl border transition-all duration-200 relative overflow-hidden ${
                          ord.status === "Cancelled"
                            ? "bg-slate-50 border-slate-100 text-slate-400 opacity-65"
                            : "bg-white border-slate-200/90 shadow-3xs hover:shadow-2xs hover:border-amber-250"
                        }`}
                      >
                        {/* Status visual tracking progress line */}
                        <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-slate-100">
                          <div className={`h-full ${getProgressBarWidth(ord.status)}`} />
                        </div>

                        <div className="flex items-start justify-between gap-2.5">
                          <span
                            className={`text-[9.5px] font-extrabold px-2 py-0.5 rounded-lg border tracking-wide uppercase font-mono ${getStatusColor(
                              ord.status
                            )}`}
                          >
                            {ord.provider}
                          </span>
                          <span className="text-[13px] font-extrabold text-slate-900 font-mono bg-slate-100 px-2 py-0.5 rounded-lg">
                            {ord.price}
                          </span>
                        </div>

                        <p
                          className={`text-xs font-semibold mt-2.5 leading-relaxed tracking-tight ${
                            ord.status === "Cancelled" ? "line-through text-slate-400" : "text-slate-800"
                          }`}
                        >
                          {ord.text}
                        </p>

                        <div className="flex items-center justify-between mt-3 pb-1 border-t border-slate-100/80 pt-2.5">
                          <span className="text-[10.5px] font-semibold text-slate-400 flex items-center gap-1 font-mono">
                            <Clock className="w-3.5 h-3.5 text-slate-350" /> {ord.time}
                          </span>

                          <div className="flex items-center gap-2">
                            {ord.status !== "Cancelled" && (
                              <button
                                onClick={() => onCancelOrder(ord.id)}
                                className="text-[10px] font-bold text-slate-450 hover:text-red-500 hover:underline cursor-pointer transition-colors"
                              >
                                Pause Subscription
                              </button>
                            )}
                            <span className="text-[10px] font-bold text-slate-400 font-mono">
                              ● {ord.status}
                            </span>
                          </div>
                        </div>
                      </div>
                    ))
                )}
              </div>
            </div>

            {/* Column B: Instant Logistics Today */}
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-2 border-b border-teal-100">
                <span className="text-[11.5px] font-extrabold text-teal-700 tracking-wider uppercase font-sans flex items-center gap-1.5">
                  🛵 Instant Portals Today
                </span>
                <span className="bg-teal-50 text-teal-750 px-2 py-0.5 rounded-full text-[9.5px] font-bold font-mono border border-teal-150">
                  {todayCount} Active Today
                </span>
              </div>

              <div className="space-y-3">
                {filteredOrders.filter((o) => o.category === "today").length === 0 ? (
                  <div className="text-center py-6 px-4 bg-slate-50/50 border border-dashed border-slate-150 rounded-2xl">
                    <p className="text-[11.5px] font-medium text-slate-400">No deliveries registered today</p>
                  </div>
                ) : (
                  filteredOrders
                    .filter((o) => o.category === "today")
                    .map((ord) => (
                      <div
                        key={ord.id}
                        className={`p-4 rounded-2xl border transition-all duration-200 relative overflow-hidden ${
                          ord.status === "Cancelled"
                            ? "bg-slate-50 border-slate-100 text-slate-400 opacity-65"
                            : "bg-white border-slate-200/90 shadow-3xs hover:shadow-2xs hover:border-teal-250"
                        }`}
                      >
                        {/* Status visual tracking progress line */}
                        <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-slate-100">
                          <div className={`h-full ${getProgressBarWidth(ord.status)}`} />
                        </div>

                        <div className="flex items-start justify-between gap-2.5">
                          <span
                            className={`text-[9.5px] font-extrabold px-2 py-0.5 rounded-lg border tracking-wide uppercase font-mono ${getStatusColor(
                              ord.status
                            )}`}
                          >
                            {ord.provider}
                          </span>
                          <span className="text-[13px] font-extrabold text-slate-900 font-mono bg-slate-100 px-2 py-0.5 rounded-lg">
                            {ord.price}
                          </span>
                        </div>

                        <p
                          className={`text-xs font-semibold mt-2.5 leading-relaxed tracking-tight ${
                            ord.status === "Cancelled" ? "line-through text-slate-400" : "text-slate-800"
                          }`}
                        >
                          {ord.text}
                        </p>

                        <div className="flex items-center justify-between mt-3 pb-1 border-t border-slate-100/80 pt-2.5">
                          <span className="text-[10.5px] font-semibold text-slate-400 flex items-center gap-1 font-mono">
                            <Clock className="w-3.5 h-3.5 text-slate-350" /> {ord.time}
                          </span>

                          <div className="flex items-center gap-2">
                            {ord.status !== "Cancelled" && ord.status !== "Delivered" && (
                              <button
                                onClick={() => onCancelOrder(ord.id)}
                                className="text-[10px] font-bold text-slate-450 hover:text-red-500 hover:underline cursor-pointer transition-colors"
                              >
                                Cancel Order
                              </button>
                            )}
                            <span className="text-[10px] font-bold text-slate-400 font-mono">
                              ● {ord.status}
                            </span>
                          </div>
                        </div>
                      </div>
                    ))
                )}
              </div>
            </div>
          </div>

          {/* Simulate checkout workspace */}
          <form onSubmit={handleSubmit} className="bg-gradient-to-br from-slate-50 to-slate-100/65 p-4.5 rounded-2xl border border-slate-200/70 shadow-3xs relative overflow-hidden">
            <div className="absolute right-0 bottom-0 w-32 h-32 bg-amber-200/10 rounded-full blur-2xl pointer-events-none" />

            <h4 className="text-[12px] font-extrabold text-slate-700 uppercase tracking-wider mb-3 flex items-center gap-1.5 font-sans">
              <Plus className="w-4 h-4 text-amber-600" /> Simulate Real-time Dispatch Checkout
            </h4>

            <div className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="block text-[10px] font-bold text-slate-450 uppercase tracking-wide mb-1.5">Portal / Provider</label>
                  <input
                    type="text"
                    value={newProvider}
                    onChange={(e) => setNewProvider(e.target.value)}
                    placeholder="E.g. Zepto, Swiggy Instamart"
                    className="w-full bg-white border border-slate-200 rounded-xl px-4 py-2.5 text-xs text-slate-850 placeholder:text-slate-400 focus:outline-none focus:border-amber-400 focus:shadow-xs transition-all"
                    required
                  />
                </div>

                <div>
                  <label className="block text-[10px] font-bold text-slate-450 uppercase tracking-wide mb-1.5">Cost Price</label>
                  <input
                    type="text"
                    value={price}
                    onChange={(e) => setPrice(e.target.value)}
                    placeholder="₹150, ₹320..."
                    className="w-full bg-white border border-slate-200 rounded-xl px-4 py-2.5 text-xs text-slate-850 placeholder:text-slate-400 focus:outline-none focus:border-amber-400 focus:shadow-xs transition-all font-mono"
                    required
                  />
                </div>

                <div>
                  <label className="block text-[10px] font-bold text-slate-450 uppercase tracking-wide mb-1.5">Frequency Schedule</label>
                  <select
                    value={category}
                    onChange={(e: any) => setCategory(e.target.value)}
                    className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2.5 text-xs text-slate-705 font-medium focus:outline-none cursor-pointer focus:border-amber-400"
                  >
                    <option value="daily">Daily Auto-Subscription</option>
                    <option value="today">Today Express Checkout</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-[10px] font-bold text-slate-450 uppercase tracking-wide mb-1.5">Line Items Specifications</label>
                <div className="flex gap-2.5">
                  <input
                    type="text"
                    value={newText}
                    onChange={(e) => setNewText(e.target.value)}
                    placeholder="E.g. Salted Butter, Fresh Bananas, Organic Honey..."
                    className="flex-1 bg-white border border-slate-200 rounded-xl px-4 py-2.5 text-xs text-slate-850 placeholder:text-slate-400 focus:outline-none focus:border-amber-400 focus:shadow-xs transition-all"
                    required
                  />
                  <button
                    type="submit"
                    className="bg-slate-900 hover:bg-slate-950 text-white text-xs font-bold px-5 rounded-xl shadow-xs transition-colors cursor-pointer shrink-0 flex items-center gap-1.5 active:scale-95"
                  >
                    Dispatch order
                  </button>
                </div>
              </div>
            </div>
          </form>
        </div>

        {/* Modal Footer */}
        <div className="border-t border-slate-100 px-6 py-4 flex items-center justify-end bg-slate-50/80">
          <button
            onClick={onClose}
            className="bg-slate-100 hover:bg-slate-200 border border-slate-200 text-slate-705 font-bold text-xs px-4.5 py-2.5 rounded-xl transition-all cursor-pointer"
          >
            Close Settings
          </button>
        </div>
      </motion.div>
    </div>
  );
}
