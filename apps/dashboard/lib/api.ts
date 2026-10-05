export class ApiError extends Error {
  constructor(
    public status: number,
    public requestId: string | null,
  ) {
    super(
      status === 503
        ? "Dependency unavailable. Retry when it recovers."
        : status === 409
          ? "State changed or an operation is pending. Refresh and recover before continuing."
          : status === 404
            ? "Record or runtime session no longer available."
            : status === 422
              ? "The request did not pass backend validation."
              : `Request failed (${status}).`,
    );
  }
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  token?: string,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) headers.Authorization = "Bearer " + token;
  const response = await fetch("/api" + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
    signal: AbortSignal.timeout(30000),
  });
  if (!response.ok)
    throw new ApiError(response.status, response.headers.get("X-Request-ID"));
  return (await response.json()) as T;
}

export const terminal = (state: string) =>
  ["COMPLETED", "FAILED"].includes(state);
export const conversational = (state: string) =>
  [
    "CREATED",
    "CONNECTING",
    "GREETING",
    "DISCOVERY",
    "QUALIFICATION",
    "SCORING",
    "DECISION",
  ].includes(state);
export const label = (value: string) =>
  value.toLowerCase().replaceAll("_", " ");
export const dateTime = (value: string) =>
  new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
