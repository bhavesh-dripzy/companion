import { useCallback, useEffect, useRef, useState } from "react";
import { getCurrentLocation, GeolocationError } from "./maps";
import type { GeoCoordinate } from "./types";

/**
 * How long a cached fix stays "fresh enough" before we silently refresh it.
 * Long enough to avoid hammering the geolocation API, short enough to be
 * useful for ETA queries from a moving user.
 */
const FRESH_TTL_MS = 60_000;

export type LocationPermissionState =
  | "unknown"
  | "prompt"
  | "granted"
  | "denied"
  | "unsupported";

interface UseUserLocationOptions {
  /** When true, attempt a silent fix on mount if permission is already granted. */
  autoCaptureWhenGranted?: boolean;
}

export interface UserLocationApi {
  location: GeoCoordinate | null;
  permission: LocationPermissionState;
  capturing: boolean;
  /** Request a fresh fix. May trigger the browser permission prompt. */
  ensureLocation: () => Promise<GeoCoordinate>;
  /** Re-query the Permissions API without prompting. */
  refreshPermission: () => Promise<void>;
}

function readPermissionState(): Promise<LocationPermissionState> {
  if (typeof navigator === "undefined" || !navigator.geolocation) {
    return Promise.resolve("unsupported");
  }
  const perms = (navigator as any).permissions;
  if (!perms?.query) {
    return Promise.resolve("unknown");
  }
  return perms
    .query({ name: "geolocation" })
    .then((status: { state: PermissionState }) => status.state as LocationPermissionState)
    .catch(() => "unknown" as LocationPermissionState);
}

export function useUserLocation({
  autoCaptureWhenGranted = true,
}: UseUserLocationOptions = {}): UserLocationApi {
  const [location, setLocation] = useState<GeoCoordinate | null>(null);
  const [permission, setPermission] = useState<LocationPermissionState>("unknown");
  const [capturing, setCapturing] = useState(false);

  // Avoid double-capturing if multiple components call ensureLocation in parallel.
  const inflight = useRef<Promise<GeoCoordinate> | null>(null);
  const lastCapturedAt = useRef<number>(0);

  const refreshPermission = useCallback(async () => {
    setPermission(await readPermissionState());
  }, []);

  const ensureLocation = useCallback(async (): Promise<GeoCoordinate> => {
    const now = Date.now();
    if (location && now - lastCapturedAt.current < FRESH_TTL_MS) {
      return location;
    }
    if (inflight.current) return inflight.current;

    setCapturing(true);
    inflight.current = getCurrentLocation()
      .then((coord) => {
        lastCapturedAt.current = Date.now();
        setLocation(coord);
        setPermission("granted");
        return coord;
      })
      .catch((err) => {
        if (err instanceof GeolocationError && err.reason === "permission_denied") {
          setPermission("denied");
        } else if (err instanceof GeolocationError && err.reason === "unsupported") {
          setPermission("unsupported");
        }
        throw err;
      })
      .finally(() => {
        setCapturing(false);
        inflight.current = null;
      });

    return inflight.current;
  }, [location]);

  useEffect(() => {
    void refreshPermission();
  }, [refreshPermission]);

  useEffect(() => {
    if (!autoCaptureWhenGranted) return;
    if (permission !== "granted") return;
    if (location) return;
    // We already have permission, so this won't prompt — just gets the fix.
    ensureLocation().catch(() => {/* swallow; UI will surface errors on demand */});
  }, [autoCaptureWhenGranted, permission, location, ensureLocation]);

  return { location, permission, capturing, ensureLocation, refreshPermission };
}
