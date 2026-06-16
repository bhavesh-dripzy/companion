// Curated "Order Food" cuisine/dish tiles shown when the user taps the
// Order Food chip. Mirrors data/groceryCategories.ts. `image_url` is a
// placeholder for now (real dish renders get dropped in later); each tile
// also carries an `emoji` that renders as a clean fallback until then.

export interface FoodCategory {
  id: string;
  label: string;
  /** What we actually search for on the food providers. */
  term: string;
  /** Per-tile fallback shown when no image (or the image fails to load). */
  emoji: string;
  /** Real dish render — leave '' to show the emoji until links are provided. */
  image_url: string;
  image_name: string;
}

export interface FoodCategoryGroup {
  title: string;
  columns: number;
  categories: FoodCategory[];
}

// Placeholder image links. Swap these for the real dish renders later — the
// tile falls back to `emoji` automatically whenever the URL is empty/broken.
const PLACEHOLDER = '';

export const FOOD_CATEGORY_GROUPS: FoodCategoryGroup[] = [
  {
    title: 'Choose a cuisine',
    columns: 4,
    categories: [
      { id: 'north-indian',  label: 'North Indian',   term: 'north indian thali',     emoji: '🍛', image_url: PLACEHOLDER, image_name: 'North Indian' },
      { id: 'biryani',       label: 'Biryani',         term: 'biryani',                emoji: '🍗', image_url: PLACEHOLDER, image_name: 'Biryani' },
      { id: 'pizza',         label: 'Pizza',           term: 'pizza',                  emoji: '🍕', image_url: PLACEHOLDER, image_name: 'Pizza' },
      { id: 'burger',        label: 'Burger',          term: 'burger',                 emoji: '🍔', image_url: PLACEHOLDER, image_name: 'Burger' },
      { id: 'momo',          label: 'Momo',            term: 'momos',                  emoji: '🥟', image_url: PLACEHOLDER, image_name: 'Momo' },
      { id: 'dosa',          label: 'Dosa',            term: 'dosa',                   emoji: '🥞', image_url: PLACEHOLDER, image_name: 'Dosa' },
      { id: 'chinese',       label: 'Chinese',         term: 'chinese noodles',        emoji: '🍜', image_url: PLACEHOLDER, image_name: 'Chinese' },
      { id: 'rolls',         label: 'Rolls',           term: 'kathi rolls',            emoji: '🌯', image_url: PLACEHOLDER, image_name: 'Rolls' },
      { id: 'paratha',       label: 'Paratha',         term: 'paratha',                emoji: '🫓', image_url: PLACEHOLDER, image_name: 'Paratha' },
      { id: 'chole-bhature', label: 'Chole\nBhature',  term: 'chole bhature',          emoji: '🍲', image_url: PLACEHOLDER, image_name: 'Chole Bhature' },
      { id: 'idli',          label: 'Idli',            term: 'idli sambar',            emoji: '🍥', image_url: PLACEHOLDER, image_name: 'Idli' },
      { id: 'pav-bhaji',     label: 'Pav Bhaji',       term: 'pav bhaji',              emoji: '🍞', image_url: PLACEHOLDER, image_name: 'Pav Bhaji' },
      { id: 'shawarma',      label: 'Shawarma',        term: 'shawarma',               emoji: '🥙', image_url: PLACEHOLDER, image_name: 'Shawarma' },
      { id: 'khichdi',       label: 'Khichdi',         term: 'khichdi',                emoji: '🥘', image_url: PLACEHOLDER, image_name: 'Khichdi' },
      { id: 'desserts',      label: 'Desserts',        term: 'desserts',               emoji: '🍨', image_url: PLACEHOLDER, image_name: 'Desserts' },
      { id: 'cake',          label: 'Cake',            term: 'cake',                   emoji: '🍰', image_url: PLACEHOLDER, image_name: 'Cake' },
      { id: 'gulab-jamun',   label: 'Gulab Jamun',     term: 'gulab jamun',            emoji: '🟤', image_url: PLACEHOLDER, image_name: 'Gulab Jamun' },
      { id: 'ice-cream',     label: 'Ice Cream',       term: 'ice cream',              emoji: '🍦', image_url: PLACEHOLDER, image_name: 'Ice Cream' },
      { id: 'tea',           label: 'Tea',             term: 'chai tea',               emoji: '☕', image_url: PLACEHOLDER, image_name: 'Tea' },
      { id: 'juice',         label: 'Juice',           term: 'fresh juice',            emoji: '🧃', image_url: PLACEHOLDER, image_name: 'Juice' },
    ],
  },
];
