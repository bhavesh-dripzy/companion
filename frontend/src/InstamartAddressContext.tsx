/**
 * Shares the user's Swiggy Instamart saved-address list + the currently
 * selected address id with the deeply-nested delivery-address bar, without
 * threading props through ChatArea → ChatMessageItem → AttachmentList.
 *
 * The list is the SAME one the Apps modal shows (`/api/food/saved-addresses/
 * ?provider=swiggy_instamart`); the selected id is `selected_instamart_address_id`
 * echoed in every agent response. The product-card attachment only carries the
 * opaque address id, so the bar resolves the human label from this context.
 */
import { createContext, useContext } from 'react';

export interface InstamartSavedAddress {
  address_id: string;
  /** "Starrrs — Bhavesh: 177p, Star residency, …" (tag + full line). */
  location_name: string;
}

export interface InstamartAddressCtx {
  addresses: InstamartSavedAddress[];
  selectedId: string | null;
  /**
   * Commit a delivery-address switch from the product-card picker. The id is the
   * authoritative new selection (sent as `selected_instamart_address_id` so the
   * backend anchors the search/cart to it instead of overriding it with the
   * stale committed address); `prompt` is the natural-language turn to run.
   */
  onSwitch?: (addressId: string, prompt: string) => void;
}

const InstamartAddressContext = createContext<InstamartAddressCtx>({
  addresses: [],
  selectedId: null,
});

export const InstamartAddressProvider = InstamartAddressContext.Provider;

export const useInstamartAddresses = (): InstamartAddressCtx =>
  useContext(InstamartAddressContext);
