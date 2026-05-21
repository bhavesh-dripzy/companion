import { api } from "./api";
import type {
  GeoCoordinate,
  MapsRouteResponse,
  MapsRouteSummary,
  MapsStatusResponse,
  MapsTravelMode,
} from "./types";

const ROUTE_ENDPOINT = "/api/maps/route";
const STATUS_ENDPOINT = "/api/maps/status/";

/**
 * Geolocation defaults tuned for "where am I, roughly?" use cases.
 * Override per-call if a specific page needs different precision.
 */
const DEFAULT_GEO_OPTIONS: PositionOptions = {
  enableHighAccuracy: false,
  timeout: 10_000,
  maximumAge: 60_000,
};

export type GeolocationFailureReason =
  | "unsupported"
  | "permission_denied"
  | "position_unavailable"
  | "timeout"
  | "unknown";

export class GeolocationError extends Error {
  reason: GeolocationFailureReason;
  constructor(reason: GeolocationFailureReason, message: string) {
    super(message);
    this.name = "GeolocationError";
    this.reason = reason;
  }
}

/** Wraps the callback-style geolocation API in a Promise with typed errors. */
export function getCurrentLocation(
  options: PositionOptions = DEFAULT_GEO_OPTIONS,
): Promise<GeoCoordinate> {
  if (typeof navigator === "undefined" || !navigator.geolocation) {
    return Promise.reject(
      new GeolocationError("unsupported", "This browser does not expose Geolocation."),
    );
  }
  return new Promise<GeoCoordinate>((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        resolve({
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          accuracyMeters: pos.coords.accuracy,
        }),
      (err) => {
        const reason: GeolocationFailureReason =
          err.code === err.PERMISSION_DENIED
            ? "permission_denied"
            : err.code === err.POSITION_UNAVAILABLE
              ? "position_unavailable"
              : err.code === err.TIMEOUT
                ? "timeout"
                : "unknown";
        reject(new GeolocationError(reason, err.message || reason));
      },
      options,
    );
  });
}

export interface ComputeRouteInput {
  destinationText?: string;
  destinationPoint?: GeoCoordinate;
  travelMode?: MapsTravelMode;
  language?: string;
  region?: string;
  /** Override the origin instead of calling the browser geolocation. */
  origin?: GeoCoordinate;
  /** Override the geolocation options when origin is auto-fetched. */
  geoOptions?: PositionOptions;
}

/**
 * Resolve the user's current location (or use the supplied override) and
 * call POST /api/maps/route. Returns the compact route summary.
 *
 * Throws ``GeolocationError`` for browser-side failures, or a regular
 * Error (with backend ``error_code`` if available) for server-side issues.
 */
export async function computeRoute(input: ComputeRouteInput): Promise<MapsRouteSummary> {
  if (!input.destinationText && !input.destinationPoint) {
    throw new Error("Provide destinationText or destinationPoint.");
  }
  const origin = input.origin ?? (await getCurrentLocation(input.geoOptions));

  const body: Record<string, unknown> = {
    origin: { latitude: origin.latitude, longitude: origin.longitude },
  };
  if (input.destinationPoint) {
    body.destination_point = {
      latitude: input.destinationPoint.latitude,
      longitude: input.destinationPoint.longitude,
    };
  } else if (input.destinationText) {
    body.destination_text = input.destinationText;
  }
  if (input.travelMode) body.travel_mode = input.travelMode;
  if (input.language) body.language = input.language;
  if (input.region) body.region = input.region;

  const response = (await api.post(ROUTE_ENDPOINT, body)) as MapsRouteResponse;
  return response.route;
}

/** Check whether the server has a Maps API key configured. Cheap pre-flight. */
export async function mapsStatus(): Promise<boolean> {
  const response = (await api.get(STATUS_ENDPOINT)) as MapsStatusResponse;
  return Boolean(response?.configured);
}
