// Curated "Discover on District" tiles shown when the user taps the
// Movies & Events chip. Mirrors data/foodCategories.ts in shape. Each tile
// carries the `prompt` it fires (District search is location-scoped — the
// backend resolves the user's saved/live location), with an `emoji` fallback
// that renders until real category art is dropped in.

export interface DistrictCategory {
  id: string;
  label: string;
  /** The District search prompt this tile fires. */
  prompt: string;
  /** Per-tile fallback shown when no image (or the image fails to load). */
  emoji: string;
  /** Real category art — leave '' to show the emoji until links are provided. */
  image_url: string;
  image_name: string;
}

export interface DistrictCategoryGroup {
  title: string;
  columns: number;
  categories: DistrictCategory[];
}

// Placeholder image links. Swap these for real category art later — the tile
// falls back to `emoji` automatically whenever the URL is empty/broken.
const PLACEHOLDER = '';

export const DISTRICT_CATEGORY_GROUPS: DistrictCategoryGroup[] = [
  {
    title: 'Movies',
    columns: 4,
    categories: [
      { id: 'movies-now',    label: 'Now\nShowing',   prompt: 'Show movies playing in cinemas near me on District',          emoji: '🎬', image_url: PLACEHOLDER, image_name: 'Now Showing' },
      { id: 'movies-imax',   label: 'IMAX &\n3D',     prompt: 'Show IMAX and 3D movies near me on District',                 emoji: '🍿', image_url: PLACEHOLDER, image_name: 'IMAX & 3D' },
      { id: 'theatres',      label: 'Cinemas',        prompt: 'Show movie theatres near me on District',                     emoji: '🎟️', image_url: PLACEHOLDER, image_name: 'Cinemas' },
    ],
  },
  {
    title: 'Events',
    columns: 4,
    categories: [
      { id: 'events-music',   label: 'Live\nMusic',    prompt: 'Find live music events near me on District',                 emoji: '🎵', image_url: PLACEHOLDER, image_name: 'Live Music' },
      { id: 'events-comedy',  label: 'Comedy',         prompt: 'Find stand-up comedy shows near me on District',             emoji: '🎤', image_url: PLACEHOLDER, image_name: 'Comedy' },
      { id: 'events-theatre', label: 'Theatre &\nPlays', prompt: 'Find theatre shows and plays near me on District',         emoji: '🎭', image_url: PLACEHOLDER, image_name: 'Theatre & Plays' },
      { id: 'events-workshop',label: 'Workshops',      prompt: 'Find workshops and activities near me on District',          emoji: '🎨', image_url: PLACEHOLDER, image_name: 'Workshops' },
      { id: 'events-all',     label: 'All\nEvents',    prompt: 'What events are happening near me this weekend on District', emoji: '✨', image_url: PLACEHOLDER, image_name: 'All Events' },
    ],
  },
  {
    title: 'Dining',
    columns: 4,
    categories: [
      { id: 'dining-book',    label: 'Book a\nTable',  prompt: 'Find popular restaurants to book a table near me on District', emoji: '🍽️', image_url: PLACEHOLDER, image_name: 'Book a Table' },
      { id: 'dining-fine',    label: 'Fine\nDining',   prompt: 'Find fine dining restaurants near me on District',           emoji: '🥂', image_url: PLACEHOLDER, image_name: 'Fine Dining' },
      { id: 'dining-cafe',    label: 'Cafés',          prompt: 'Find cafés near me on District',                             emoji: '☕', image_url: PLACEHOLDER, image_name: 'Cafés' },
      { id: 'dining-bars',    label: 'Bars &\nPubs',   prompt: 'Find bars and pubs near me on District',                     emoji: '🍸', image_url: PLACEHOLDER, image_name: 'Bars & Pubs' },
    ],
  },
];
