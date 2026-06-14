// Curated grocery categories for the in-chat picker (issue #59).
//
// Neither the Swiggy Instamart nor the Zepto MCP exposes a category /
// taxonomy listing tool — both are pure `search_products` surfaces. So the
// browse categories users expect are hardcoded here. Tapping a tile fires a
// normal search turn that fans out to BOTH providers via `term`, reusing the
// existing onQuickPrompt → /api/agent/query/ flow (no backend change).
//
// `image_url` is a real representative product image lifted from the live MCP
// search dumps in `outputs/instamart/search_products_*.json` (Swiggy's
// `media-assets.swiggy.com` CDN is public / hotlinkable). To add a category,
// dump a `search_products` run for its keyword (see scripts/dump_zepto_outputs.py
// for the pattern) and copy the first product's `imageUrl` here. `emoji` is
// only a fallback if the image fails to load, so the grid never looks broken.

export interface GroceryCategory {
  /** Stable key. */
  id: string;
  /** Tile label shown to the user. */
  label: string;
  /** Search phrase sent to both providers when the tile is tapped. */
  term: string;
  /** Emoji fallback when `image_url` is absent / fails to load. */
  emoji: string;
  /** Representative product image (Swiggy CDN). */
  image_url?: string;
}

// Each `image_url` is the first result from the matching
// outputs/instamart/search_products_<keyword>.json dump.
export const GROCERY_CATEGORIES: GroceryCategory[] = [
  {
    id: 'fruits',
    label: 'Fruits',
    term: 'fresh fruits',
    emoji: '🍎',
    image_url:
      'https://media-assets.swiggy.com/swiggy/image/upload/NI_CATALOG/IMAGES/CIW/2025/12/22/7fa61c63-4105-4f41-88bf-bd469a8bf3c5_1005.jpg',
  },
  {
    id: 'milk',
    label: 'Milk',
    term: 'milk',
    emoji: '🥛',
    image_url:
      'https://media-assets.swiggy.com/swiggy/image/upload/NI_CATALOG/IMAGES/ciw/2025/12/17/b904f8df-ae15-417d-b161-ae3615197d62_S8PF9T8YLG_MN_17122025.png',
  },
  {
    id: 'bread',
    label: 'Bread',
    term: 'bread',
    emoji: '🍞',
    image_url:
      'https://media-assets.swiggy.com/swiggy/image/upload/NI_CATALOG/IMAGES/ciw/2025/12/17/b2ebb232-8080-440e-a0cf-63e5e7edfd7b_IG9SZ27L6D_MN_17122025.png',
  },
  {
    id: 'eggs',
    label: 'Eggs',
    term: 'eggs',
    emoji: '🥚',
    image_url:
      'https://media-assets.swiggy.com/swiggy/image/upload/NI_CATALOG/IMAGES/CIW/2026/2/9/dffb4058-75aa-46cb-af29-b2a93810e776_839704_1.png',
  },
  {
    id: 'atta',
    label: 'Atta',
    term: 'atta',
    emoji: '🌾',
    image_url:
      'https://media-assets.swiggy.com/swiggy/image/upload/NI_CATALOG/IMAGES/CIW/2026/3/23/6d490b8b-b02b-4347-ba2f-455cf295f0bf_15558_1.png',
  },
  {
    id: 'paneer',
    label: 'Paneer',
    term: 'paneer',
    emoji: '🧀',
    image_url:
      'https://media-assets.swiggy.com/swiggy/image/upload/NI_CATALOG/IMAGES/CIW/2024/10/10/9353037b-cf00-41b3-af64-3060d1167ab3_2446.png',
  },
  {
    id: 'cold-drinks',
    label: 'Cold Drinks',
    term: 'cold drinks',
    emoji: '🥤',
    image_url:
      'https://media-assets.swiggy.com/swiggy/image/upload/NI_CATALOG/IMAGES/CIW/2025/11/28/69bafb1f-5675-4c4a-b65b-995d2d9f1f50_210.png',
  },
  {
    id: 'detergent',
    label: 'Detergent',
    term: 'detergent',
    emoji: '🧴',
    image_url:
      'https://media-assets.swiggy.com/swiggy/image/upload/NI_CATALOG/IMAGES/CIW/2025/9/21/4d78bb95-8a58-4711-963a-2fb6fe180aac_83826_1.png',
  },
];
