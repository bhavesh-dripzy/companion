export interface GroceryCategory {
  id: string;
  label: string;
  term: string;
  image_url: string;
  image_name: string;
}

export interface GroceryCategoryGroup {
  title: string;
  columns: number;
  categories: GroceryCategory[];
}

export const GROCERY_CATEGORY_GROUPS: GroceryCategoryGroup[] = [
  {
    title: 'Choose a category',
    columns: 4,
    categories: [
      {
        id: 'vegetables',
        label: 'Fresh\nVegetables',
        term: 'fresh vegetables',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-160-161,pr-true,f-auto,q-auto/cms/sub_category/694c07e0-542b-49db-a596-b1f4f4935342.png',
        image_name: 'Fresh Vegetables',
      },
      {
        id: 'fruits',
        label: 'Fresh Fruits',
        term: 'fresh fruits',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-160-161,pr-true,f-auto,q-auto/cms/sub_category/7e51d0f6-ee57-42f3-98f9-945033ad3e5f.png',
        image_name: 'Fresh Fruits',
      },
      {
        id: 'milk',
        label: 'Milk',
        term: 'milk dairy',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-121,pr-true,f-auto,q-auto/cms/sub_category/87.png',
        image_name: 'Milk',
      },
      {
        id: 'bread',
        label: 'Bread',
        term: 'bread',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-121,pr-true,f-auto,q-auto/cms/sub_category/85.png',
        image_name: 'Bread',
      },
      {
        id: 'atta',
        label: 'Atta',
        term: 'atta wheat flour',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-120,pr-true,f-auto,q-auto/cms/sub_category/30388500-f133-4f7f-8e15-2b2c5ec804a7.png',
        image_name: 'Atta',
      },
      {
        id: 'oil',
        label: 'Oil & Ghee',
        term: 'cooking oil ghee',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-121,pr-true,f-auto,q-auto/cms/sub_category/19.png',
        image_name: 'Oil & Ghee',
      },
      {
        id: 'sooji',
        label: 'Sooji',
        term: 'sooji semolina rava',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-120,pr-true,f-auto,q-auto/cms/sub_category/766a8e53-bf65-498e-9b51-5c6829f8dcb2.png',
        image_name: 'Sooji',
      },
      {
        id: 'besan',
        label: 'Besan',
        term: 'besan gram flour',
        image_url: 'https://cdn.zeptonow.com/production/ik-seo/tr:w-400,ar-1200-1200,pr-true,f-auto,q-auto/cms/product_variant/a2b47051-6275-4804-99d2-2c88caa8301b/Daily-Good-Besan-Gram-Flour-Chana-Besan.jpeg',
        image_name: 'Besan',
      },
      {
        id: 'soft-drinks',
        label: 'Soft Drinks',
        term: 'soft drinks cold drinks soda',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-120,pr-true,f-auto,q-auto/cms/sub_category/f9127a14-d833-4898-9af5-0504b1118273.png',
        image_name: 'Soft Drinks',
      },
      {
        id: 'chicken',
        label: 'Chicken',
        term: 'chicken',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-121,pr-true,f-auto,q-auto/cms/sub_category/118.png',
        image_name: 'Chicken',
      },
      {
        id: 'fish',
        label: 'Fish &\nSeafood',
        term: 'fish seafood prawns',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-121,pr-true,f-auto,q-auto/cms/sub_category/119.png',
        image_name: 'Fish & Seafood',
      },
      {
        id: 'masala',
        label: 'Masala &\nPowder',
        term: 'masala spices powder',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-121,pr-true,f-auto,q-auto/cms/sub_category/45.png',
        image_name: 'Masala & Powder',
      },
      {
        id: 'tea',
        label: 'Tea',
        term: 'tea',
        image_url: 'https://cdn.zeptonow.com/production/ik-seo/tr:w-400,ar-1200-1200,pr-true,f-auto,q-auto/cms/product_variant/f9525a33-4034-45b8-b4a1-0935c262e47c/Brooke-Bond-Red-Label-Tea.jpg',
        image_name: 'Tea',
      },
      {
        id: 'coffee',
        label: 'Coffee',
        term: 'coffee',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-980-980,pr-true,f-auto,q-auto/cms/sub_category/a4d0523d-5d4d-49f4-87cc-82b6e3922d2a.jpeg',
        image_name: 'Coffee',
      },
      {
        id: 'chips',
        label: 'Chips',
        term: 'chips crisps',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-121,pr-true,f-auto,q-auto/cms/sub_category/106.png',
        image_name: 'Chips',
      },
      {
        id: 'healthy-munchies',
        label: 'Healthy\nMunchies',
        term: 'healthy snacks munchies',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-120,pr-true,f-auto,q-auto/cms/sub_category/2632c69f-92ee-4209-a4bd-58f30be06b14.png',
        image_name: 'Healthy Munchies',
      },
      {
        id: 'ice-cream',
        label: 'Ice Creams',
        term: 'ice cream',
        image_url: 'https://cdn.zeptonow.com/production/ik-seo/tr:w-400,ar-1200-1200,pr-true,f-auto,q-auto/cms/product_variant/196b3f80-59f0-461d-ad77-6046f21fc355/Amul-Fruit-N-Nut-Fantasy-Ice-Cream-Tub-Creamy-Indulgent.jpeg',
        image_name: 'Ice Creams',
      },
      {
        id: 'chocolates',
        label: 'Chocolates',
        term: 'chocolates',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-272-322,pr-true,f-auto,q-auto/cms/sub_category/524656d4-66be-45ab-8328-647e21033697.jpeg',
        image_name: 'Chocolates',
      },
      {
        id: 'biscuits',
        label: 'Biscuits',
        term: 'biscuits cookies',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-120,pr-true,f-auto,q-auto/cms/sub_category/181.png',
        image_name: 'Biscuits',
      },
      {
        id: 'cereals',
        label: 'Cereals',
        term: 'cereals oats breakfast',
        image_url: 'https://cdn.zeptonow.com/production/tr:w-400,ar-120-120,pr-true,f-auto,q-auto/cms/sub_category/155.png',
        image_name: 'Cereals',
      },
    ],
  },
];
