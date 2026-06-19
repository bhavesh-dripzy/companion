export interface FoodCategory {
  id: string;
  label: string;
  /** What we actually search for on the food providers. */
  term: string;
  /** Kept for backward compatibility. */
  emoji: string;
  /** Category image shown in the tile. */
  image_url: string;
  image_name: string;
}

export interface FoodCategoryGroup {
  title: string;
  columns: number;
  categories: FoodCategory[];
}

export const FOOD_CATEGORY_GROUPS: FoodCategoryGroup[] = [
  {
    title: 'Order Food',
    columns: 4,
    categories: [
      {
        id: 'north-indian',
        label: 'North Indian',
        term: 'north indian thali',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/North%20Indian.png',
        image_name: 'North Indian',
      },
      {
        id: 'biryani',
        label: 'Biryani',
        term: 'biryani',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/Biryani.png',
        image_name: 'Biryani',
      },
      {
        id: 'pizza',
        label: 'Pizza',
        term: 'pizza',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/Pizza.png',
        image_name: 'Pizza',
      },
      {
        id: 'burger',
        label: 'Burger',
        term: 'burger',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/Burger.png',
        image_name: 'Burger',
      },
      {
        id: 'noodles',
        label: 'Noodles',
        term: 'noodles',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/Noodles.png',
        image_name: 'Noodles',
      },
      {
        id: 'chinese',
        label: 'Chinese',
        term: 'chinese food',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/Chinese.png',
        image_name: 'Chinese',
      },
      {
        id: 'rolls',
        label: 'Rolls',
        term: 'kathi rolls',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/Rolls.png',
        image_name: 'Rolls',
      },
      {
        id: 'paratha',
        label: 'Paratha',
        term: 'paratha',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/Paratha.png',
        image_name: 'Paratha',
      },
      {
        id: 'coffee',
        label: 'Coffee',
        term: 'coffee',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/Coffee.png',
        image_name: 'Coffee',
      },
      {
        id: 'cake',
        label: 'Cake',
        term: 'cake',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/Cake.png',
        image_name: 'Cake',
      },
      {
        id: 'pasta',
        label: 'Pasta',
        term: 'pasta',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/Pasta.png',
        image_name: 'Pasta',
      },
      {
        id: 'shake',
        label: 'Shake',
        term: 'milkshake',
        emoji: '',
        image_url:
          'https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto/PC_Mweb/Shake.png',
        image_name: 'Shake',
      },
    ],
  },
];