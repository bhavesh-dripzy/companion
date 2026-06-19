/**
 * Shares the user's Zepto saved-address list + the currently selected address
 * id with the deeply-nested delivery-address bar, without threading props
 * through ChatArea → ChatMessageItem → AttachmentList.
 *
 * Mirrors InstamartAddressContext: Zepto's `select_saved_address` binds the
 * store AND the order doorstep from a saved-address id, so — just like Instamart
 * — what we show below the cards is the saved address's label, resolved from the
 * list fetched via `/api/food/saved-addresses/?provider=zepto` plus the
 * `selected_zepto_address_id` echoed in every agent response.
 */
import { createContext, useContext } from 'react';

export interface ZeptoSavedAddress {
  address_id: string;
  /** "work — hotel maharaja, Sector 38 Rd, …" (tag + line). */
  location_name: string;
}

export interface ZeptoAddressCtx {
  addresses: ZeptoSavedAddress[];
  selectedId: string | null;
}

const ZeptoAddressContext = createContext<ZeptoAddressCtx>({
  addresses: [],
  selectedId: null,
});

export const ZeptoAddressProvider = ZeptoAddressContext.Provider;

export const useZeptoAddresses = (): ZeptoAddressCtx => useContext(ZeptoAddressContext);
