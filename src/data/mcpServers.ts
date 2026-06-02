/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { McpServer } from "../types";

export const initialMcpServers: McpServer[] = [
  {
    id: "zomato",
    name: "Zomato MCP",
    iconText: "Z",
    iconBgColor: "bg-red-600",
    iconTextColor: "text-white",
    toolsCount: 11,
    description: "Connects to Zomato's food ordering and dining discovery platform. Allows searching restaurants, viewing menus, reviews, and managing active orders.",
    connected: true,
    tools: [
      {
        name: "zomato_search_restaurants",
        description: "Search for restaurants nearby or in a specific location on Zomato",
        parameters: {
          type: "OBJECT",
          properties: {
            cuisine: { type: "STRING", description: "Type of cuisine (e.g. Italian, Indian, South Indian, Pizza, Chinese)" },
            location: { type: "STRING", description: "City or locality (default to current location if omitted)" },
            max_price: { type: "NUMBER", description: "Maximum budget/price rating range (1-4)" }
          },
          required: []
        }
      },
      {
        name: "zomato_get_restaurant_menu",
        description: "Fetch menu items and prices for a specific restaurant ID",
        parameters: {
          type: "OBJECT",
          properties: {
            restaurant_id: { type: "STRING", description: "ID of the restaurant, like '12345'" }
          },
          required: ["restaurant_id"]
        }
      },
      {
        name: "zomato_place_order",
        description: "Place a food delivery order from a restaurant on Zomato",
        parameters: {
          type: "OBJECT",
          properties: {
            restaurant_id: { type: "STRING", description: "The restaurant ID" },
            food_items: { type: "STRING", description: "Comma-separated list of item names and quantities (e.g. 'Biryani x2, Coke x1')" },
            delivery_address: { type: "STRING", description: "User's full delivery address" }
          },
          required: ["restaurant_id", "food_items", "delivery_address"]
        }
      },
      {
        name: "zomato_get_order_status",
        description: "Get current live delivery status of an active Zomato order",
        parameters: {
          type: "OBJECT",
          properties: {
            order_id: { type: "STRING", description: "The order ID" }
          },
          required: ["order_id"]
        }
      }
    ]
  },
  {
    id: "swiggy_food",
    name: "Swiggy Food MCP",
    iconText: "S",
    iconBgColor: "bg-orange-500",
    iconTextColor: "text-white",
    toolsCount: 14,
    description: "Swiggy's core food delivery connector. Offers local restaurant discovery, ratings, deal matching, checkout integration, and live rider tracking.",
    connected: true,
    tools: [
      {
        name: "swiggy_search_food",
        description: "Find available dishes and food items across restaurants on Swiggy",
        parameters: {
          type: "OBJECT",
          properties: {
            query: { type: "STRING", description: "Dish or restaurant name, e.g. 'Butter Chicken', 'Biryani'" },
            sort_by: { type: "STRING", enum: ["relevance", "delivery_time", "rating", "cost_low_to_high"], description: "Sorting criteria" }
          },
          required: ["query"]
        }
      },
      {
        name: "swiggy_get_offers",
        description: "List the active discounts, promo codes, and deals for food delivery",
        parameters: {
          type: "OBJECT",
          properties: {
            restaurant_id: { type: "STRING", description: "Optional restaurant ID to get restaurant-specific coupons" }
          },
          required: []
        }
      }
    ]
  },
  {
    id: "district",
    name: "District MCP",
    iconText: "D",
    iconBgColor: "bg-indigo-600",
    iconTextColor: "text-white",
    toolsCount: 11,
    description: "Local neighborhood and community activity connector. Provides info on local events, government updates, neighborhood hubs, and shared facilities.",
    connected: true,
    tools: [
      {
        name: "district_get_events",
        description: "Get listing of upcoming events and gatherings in the user's community district",
        parameters: {
          type: "OBJECT",
          properties: {
            zipcode: { type: "STRING", description: "Neighborhood postal zip code" }
          },
          required: ["zipcode"]
        }
      }
    ]
  },
  {
    id: "blinkit",
    name: "Blinkit MCP",
    iconText: "B",
    iconBgColor: "bg-yellow-400",
    iconTextColor: "text-black",
    toolsCount: 8,
    description: "Instant commerce and 10-minute delivery system for fresh groceries, emergency supplies, electronics, and home wellness brands.",
    connected: false,
    tools: [
      {
        name: "blinkit_quick_order",
        description: "Order essential grocery items instantly on Blinkit",
        parameters: {
          type: "OBJECT",
          properties: {
            groceries: { type: "STRING", description: "Grocery items description" }
          },
          required: ["groceries"]
        }
      }
    ]
  },
  {
    id: "zepto",
    name: "Zepto MCP",
    iconText: "Z",
    iconBgColor: "bg-purple-600",
    iconTextColor: "text-white",
    toolsCount: 13,
    description: "Superfast 10-minute grocery delivery. Allows checking items stock, adding groceries, booking standard/vip slots, and fetching live ETA times.",
    connected: false,
    tools: [
      {
        name: "zepto_check_stock",
        description: "Check if a particular item is in stock inside the nearest Zepto micro-warehouse",
        parameters: {
          type: "OBJECT",
          properties: {
            item: { type: "STRING", description: "Item description, e.g., 'Amul Butter 500g', 'Organic Tomato'" }
          },
          required: ["item"]
        }
      }
    ]
  },
  {
    id: "swiggy_instamart",
    name: "Swiggy Instamart ...",
    iconText: "I",
    iconBgColor: "bg-slate-800",
    iconTextColor: "text-white",
    toolsCount: 10,
    description: "Instant grocery delivery platform that connects Swiggy dark stores to purchase household goods and food items with 10-minute ETA.",
    connected: true,
    tools: [
      {
        name: "instamart_quick_search",
        description: "Search for groceries on Swiggy Instamart",
        parameters: {
          type: "OBJECT",
          properties: {
            item_name: { type: "STRING", description: "Grocery item name, e.g. 'milk', 'bananas', 'bread'" },
            quantity_required: { type: "NUMBER", description: "Number of units required" }
          },
          required: ["item_name"]
        }
      }
    ]
  },
  {
    id: "zerodha_kite",
    name: "Zerodha Kite MCP",
    iconText: "K",
    iconBgColor: "bg-red-500",
    iconTextColor: "text-white",
    toolsCount: 16,
    description: "Integrates with Zerodha's Kite platform for Indian equities, derivatives, mutual funds and commodity trading. Fetches stock pricing tables and updates holdings.",
    connected: false,
    tools: [
      {
        name: "kite_get_live_price",
        description: "Get the current trading price/quote for a stock on National Stock Exchange (NSE)",
        parameters: {
          type: "OBJECT",
          properties: {
            ticker: { type: "STRING", description: "Ticker symbol, e.g., 'TCS', 'RELIANCE', 'HDFCBANK'" }
          },
          required: ["ticker"]
        }
      },
      {
        name: "kite_buy_shares",
        description: "Place a buy order for shares of a stock via Zerodha Kite API",
        parameters: {
          type: "OBJECT",
          properties: {
            ticker: { type: "STRING", description: "Ticker symbol" },
            quantity: { type: "NUMBER", description: "Number of shares to buy" },
            order_type: { type: "STRING", enum: ["MARKET", "LIMIT"], description: "Standard order style" }
          },
          required: ["ticker", "quantity", "order_type"]
        }
      }
    ]
  },
  {
    id: "groww",
    name: "Groww MCP",
    iconText: "G",
    iconBgColor: "bg-teal-600",
    iconTextColor: "text-white",
    toolsCount: 12,
    description: "Enables programmatic exploration of mutual fund investment options, historical SIP (Systematic Investment Plans) returns, and automated monthly deposits.",
    connected: false,
    tools: [
      {
        name: "groww_sip_calculator",
        description: "Calculate expected mutual fund returns based on monthly SIP and duration",
        parameters: {
          type: "OBJECT",
          properties: {
            monthly_investment: { type: "NUMBER", description: "Monthly investment in INR" },
            return_rate: { type: "NUMBER", description: "Expected annual return rate in percentage, e.g., 12 for 12%" },
            years: { type: "NUMBER", description: "Investment timeline in years" }
          },
          required: ["monthly_investment", "years"]
        }
      }
    ]
  },
  {
    id: "google_maps",
    name: "Google Maps MCP",
    iconText: "M",
    iconBgColor: "bg-blue-500",
    iconTextColor: "text-white",
    toolsCount: 15,
    description: "Provides geographical capabilities, distance estimates, transit matrices, routing logic, and live traffic metrics.",
    connected: true,
    tools: [
      {
        name: "maps_get_directions",
        description: "Calculate travel time and turn-by-turn directions between locations",
        parameters: {
          type: "OBJECT",
          properties: {
            from: { type: "STRING", description: "Starting point address or coordinates" },
            to: { type: "STRING", description: "Destination address or coordinates" }
          },
          required: ["from", "to"]
        }
      }
    ]
  },
  {
    id: "google_calendar",
    name: "Google Calendar MCP",
    iconText: "C",
    iconBgColor: "bg-blue-600",
    iconTextColor: "text-white",
    toolsCount: 9,
    description: "Allows reviewing meeting schedules, booking dynamic appointment slots, and updating timeline milestones in real-time.",
    connected: true,
    tools: [
      {
        name: "calendar_list_events",
        description: "Get listing of planned calendar items for a given time window",
        parameters: {
          type: "OBJECT",
          properties: {
            date: { type: "STRING", description: "Target Date query (YYYY-MM-DD)" }
          },
          required: ["date"]
        }
      }
    ]
  },
  {
    id: "phone_calls",
    name: "Phone Calls MCP",
    iconText: "P",
    iconBgColor: "bg-emerald-500",
    iconTextColor: "text-white",
    toolsCount: 5,
    description: "Integrates with cellular pathways to trigger automatic ringout sequences, dial emergency backplanes, or retrieve missed voicemail logs.",
    connected: true,
    tools: [
      {
        name: "phone_trigger_dial",
        description: "Place a cellular telephone call to a destination recipient",
        parameters: {
          type: "OBJECT",
          properties: {
            number: { type: "STRING", description: "Phone number dial string" },
            say: { type: "STRING", description: "Custom speech text synthetic readout" }
          },
          required: ["number"]
        }
      }
    ]
  },
  {
    id: "whatsapp",
    name: "WhatsApp MCP",
    iconText: "W",
    iconBgColor: "bg-green-500",
    iconTextColor: "text-white",
    toolsCount: 8,
    description: "Double-encrypted secure instant correspondence service to transmit messages, check unread threads, and trigger broadcasts.",
    connected: true,
    tools: [
      {
        name: "whatsapp_send_text",
        description: "Send direct chat message to a contact's phone link",
        parameters: {
          type: "OBJECT",
          properties: {
            recipient: { type: "STRING", description: "Contact number or name" },
            message: { type: "STRING", description: "Body of text message to transmit" }
          },
          required: ["recipient", "message"]
        }
      }
    ]
  },
  {
    id: "expedia",
    name: "Expedia MCP",
    iconText: "E",
    iconBgColor: "bg-yellow-600",
    iconTextColor: "text-white",
    toolsCount: 14,
    description: "Comprehensive reservation finder platform to lookup flights, compare resort rentals, track hotel bookings, and bundle travel deals.",
    connected: false,
    tools: [
      {
        name: "expedia_search_hotels",
        description: "Compare prices and ratings for top-reviewed hotels in a specific city",
        parameters: {
          type: "OBJECT",
          properties: {
            location: { type: "STRING", description: "Destination city name" },
            check_in_date: { type: "STRING", description: "Check-in Date (YYYY-MM-DD)" }
          },
          required: ["location"]
        }
      }
    ]
  }
];

export const mockToolExecutions: Record<string, (args: any) => string> = {
  zomato_search_restaurants: (args) => {
    const cuisine = args.cuisine || "multi-cuisine";
    const loc = args.location || "Bengaluru";
    return JSON.stringify([
      { id: "z1", name: `The Imperial ${cuisine} Diner`, rating: 4.8, reviews: 231, cost: "₹₹₹", location: loc, eta: "25 mins" },
      { id: "z2", name: `Royal ${cuisine} Feast`, rating: 4.6, reviews: 148, cost: "₹₹", location: loc, eta: "15 mins" },
      { id: "z3", name: `Corner Spot (Best for ${cuisine})`, rating: 4.3, reviews: 89, cost: "₹", location: loc, eta: "30 mins" }
    ], null, 2);
  },
  zomato_get_restaurant_menu: (args) => {
    return JSON.stringify({
      restaurant_id: args.restaurant_id,
      name: "The Imperial Diner",
      popular_items: [
        { item: "Lucknowi Chicken Biryani", price: "₹299", rating: "★ 4.9" },
        { item: "Paneer Butter Masala", price: "₹240", rating: "★ 4.7" },
        { item: "Garlic Butter Naan", price: "₹65", rating: "★ 4.8" },
        { item: "Premium Chocolate Slices", price: "₹180", rating: "★ 4.5" }
      ]
    }, null, 2);
  },
  zomato_place_order: (args) => {
    return JSON.stringify({
      order_id: "ZOM-" + Math.floor(100000 + Math.random() * 900000),
      status: "Dispatched",
      items_placed: args.food_items,
      address: args.delivery_address,
      eta: "18 minutes",
      total: "₹385"
    }, null, 2);
  },
  zomato_get_order_status: (args) => {
    return JSON.stringify({
      order_id: args.order_id,
      status: "Out for Delivery",
      rider_name: "Ramesh Kumar (+91 98765 43210)",
      live_location: "Near 8th Main Cross Rd, Indiranagar",
      last_updated: "2 mins ago",
      eta: "7 mins"
    }, null, 2);
  },
  swiggy_search_food: (args) => {
    const q = args.query || "Biryani";
    return JSON.stringify([
      { restaurant: "Paradise Biryani", dish: `Special ${q}`, price: "₹320", rating: "★4.5", delivery_time: "20 mins" },
      { restaurant: "Biryani Zone", dish: `Classic Hyderabadi ${q}`, price: "₹280", rating: "★4.4", delivery_time: "15 mins" },
      { restaurant: "Empire Restaurant", dish: `Kabab & Ghee ${q} Platter`, price: "₹350", rating: "★4.6", delivery_time: "25 mins" }
    ], null, 2);
  },
  swiggy_get_offers: (args) => {
    return JSON.stringify([
      { code: "SWIGGY50", description: "Get 50% flat discount up to ₹100", min_cart: "₹199" },
      { code: "JUMBO120", description: "Get ₹120 flat discount on orders from top brands", min_cart: "₹350" },
      { code: "FREEDEL", description: "Free delivery on orders above ₹200 within 5km", min_cart: "₹200" }
    ], null, 2);
  },
  instamart_quick_search: (args) => {
    const name = args.item_name || "organics";
    return JSON.stringify({
      search_term: name,
      available_items: [
        { brand: "MilkyMist", item: `${name} fresh pack`, price: "₹45", stock: "14 left", eta: "8 mins" },
        { brand: "Standard Farms", item: `Fresh premium ${name}`, price: "₹80", stock: "6 left", eta: "11 mins" }
      ]
    }, null, 2);
  },
  district_get_events: (args) => {
    const code = args.zipcode || "560038";
    return JSON.stringify([
      { event: "Indiranagar Weekend Organic Market", time: "Saturday 8:00 AM", location: "District Hub ground", cost: "Free" },
      { event: "Community Tree Plantation & Clean-up Drive", time: "Sunday 7:30 AM", location: "Binnamangala Stage 2", host: "Namma District Civic Association" },
      { event: "Under-19 Neighborhood Cricket Championship", time: "Sunday 10:00 AM", location: "District Playground", registration: "Closed" }
    ], null, 2);
  },
  zepto_check_stock: (args) => {
    const item = args.item || "Apples";
    return JSON.stringify({
      store_id: "ZEP-IND-90",
      item_queried: item,
      is_available: true,
      in_stock_quantity: 48,
      price: "₹120 / kg",
      delivery_window: "10 mins (Fast Track)"
    }, null, 2);
  },
  kite_get_live_price: (args) => {
    const ticker = (args.ticker || "RELIANCE").toUpperCase();
    const prices: Record<string, number> = { RELIANCE: 2452.80, TCS: 3740.10, HDFCBANK: 1530.15, INFOSYS: 1412.30, TATAMOTORS: 948.40 };
    const price = prices[ticker] || (Math.random() * 2000 + 100);
    const change = (Math.random() * 4 - 2).toFixed(2);
    return JSON.stringify({
      instrument: ticker,
      exchange: "NSE",
      last_price: `₹${price.toFixed(2)}`,
      day_change_percent: `${change}%`,
      open: `₹${(price * 0.99).toFixed(2)}`,
      high: `₹${(price * 1.01).toFixed(2)}`,
      low: `₹${(price * 0.985).toFixed(2)}`,
      volume: Math.floor(100000 + Math.random() * 500000)
    }, null, 2);
  },
  kite_buy_shares: (args) => {
    const qty = args.quantity || 1;
    const tick = (args.ticker || "NIFTY").toUpperCase();
    return JSON.stringify({
      status: "COMPLETED",
      order_id: "KITE-ORD-" + Math.floor(10000000 + Math.random() * 90000000),
      trading_symbol: tick,
      quantity_ordered: qty,
      order_type: args.order_type || "MARKET",
      execution_price: "₹" + (Math.random() * 1000 + 500).toFixed(2),
      transaction_time: "Just now",
      brokerage_estimate: "₹0.00 (Equity Delivery free)"
    }, null, 2);
  },
  groww_sip_calculator: (args) => {
    const monthly = args.monthly_investment || 5000;
    const dur = args.years || 10;
    const rate = args.return_rate || 12;
    // compound interest formula for SIP
    const i = (rate / 100) / 12;
    const numMonths = dur * 12;
    const totalInvested = monthly * numMonths;
    const estReturns = monthly * (((Math.pow(1 + i, numMonths) - 1) / i) * (1 + i)) - totalInvested;
    const totalValue = totalInvested + estReturns;

    return JSON.stringify({
      caculation: "Monthly SIP Growth Estimation",
      monthly_investment_inr: `₹${monthly}`,
      duration_years: dur,
      expected_rate: `${rate}% p.a.`,
      total_amount_invested: `₹${totalInvested.toLocaleString()}`,
      estimated_gain: `₹${Math.round(estReturns).toLocaleString()}`,
      total_projected_value: `₹${Math.round(totalValue).toLocaleString()}`
    }, null, 2);
  },
  tracker_log_meal: (args) => {
    const meal = args.meal_desc || "Plate of dinner";
    return JSON.stringify({
      meal_recognized: meal,
      nutrition_summary: {
        calories: "340 kcal",
        protein: "14g",
        carbohydrates: "42g",
        fats: "11g"
      },
      daily_totals_after_add: {
        calories_consumed: "1,240 kcal",
        target_remaining: "560 kcal",
        water_intake_ml: "1,800 ml"
      }
    }, null, 2);
  }
};
