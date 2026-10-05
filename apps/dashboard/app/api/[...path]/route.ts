import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  if (
    path.some((p) => !/^[a-zA-Z0-9_-]+$/.test(p)) ||
    !(
      path[0] === "v1" ||
      (path[0] === "voice" && path[1] === "sessions") ||
      (path.length === 1 && path[0] === "health")
    )
  ) {
    return NextResponse.json(
      { detail: "API path unavailable" },
      { status: 404 },
    );
  }
  const origin = request.headers.get("origin");
  const publicOrigin = `${request.nextUrl.protocol}//${request.headers.get("host")}`;
  if (request.method !== "GET" && origin && origin !== publicOrigin) {
    return NextResponse.json(
      { detail: "invalid request origin" },
      { status: 403 },
    );
  }
  try {
    const gateway = new URL(
      process.env.GATEWAY_URL || "http://127.0.0.1:18000",
    );
    const target = new URL("/" + path.join("/"), gateway);
    target.search = request.nextUrl.search;
    const headers = new Headers({
      "Content-Type": "application/json",
      "X-Request-ID": crypto.randomUUID(),
    });
    const authorization = request.headers.get("authorization");
    if (path[0] === "voice" && authorization)
      headers.set("Authorization", authorization);
    let body: string | undefined;
    if (request.method !== "GET") {
      body = await request.text();
      if (new TextEncoder().encode(body).length > 131072) {
        return NextResponse.json(
          { detail: "request too large" },
          { status: 413 },
        );
      }
      if (!body) body = undefined;
    }
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      signal: AbortSignal.timeout(25000),
      cache: "no-store",
      redirect: "error",
    });
    const response = await upstream.arrayBuffer();
    if (response.byteLength > 8388608) throw new Error("response limit");
    return new NextResponse(response, {
      status: upstream.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Request-ID":
          upstream.headers.get("X-Request-ID") || headers.get("X-Request-ID")!,
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "Gateway unavailable. Retry after it recovers." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}

export { proxy as GET, proxy as POST, proxy as PATCH, proxy as DELETE };
