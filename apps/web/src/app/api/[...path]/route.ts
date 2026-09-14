import type { NextRequest } from "next/server";

/**
 * Proxies every `/api/*` call to the recovery engine, at request time.
 *
 * This used to be a `rewrites()` entry in next.config.ts, which Next evaluates
 * when it builds: the destination is compiled into `routes-manifest.json`. The
 * Docker image is built without `API_ORIGIN`, so the target froze as
 * `http://localhost:8000` — the web container itself — and setting the variable
 * in compose at runtime changed nothing. Every request came back 500 with no
 * body to say why.
 *
 * A route handler reads the environment per request, so the same image runs
 * locally and in production with only the variable changing.
 *
 * The SSE route at /api/recovery-missions/[id]/stream is more specific and
 * still wins over this catch-all; it needs its own handling to stay unbuffered.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const fetchCache = "force-no-store";

const API_ORIGIN = () => process.env.API_ORIGIN ?? "http://localhost:8000";

/** Headers worth carrying upstream. Hop-by-hop and host headers are dropped. */
const FORWARD = ["content-type", "accept", "idempotency-key", "authorization"];

async function proxy(request: NextRequest, path: string[]) {
  const origin = API_ORIGIN();
  const target = `${origin}/api/${path.join("/")}${request.nextUrl.search}`;

  const headers = new Headers();
  for (const name of FORWARD) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.text();

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: request.signal,
    });

    const payload = await upstream.arrayBuffer();
    return new Response(payload, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch (error) {
    // Name what could not be reached. A bare 500 sent the last deployment
    // hunting through three layers for a variable that was never read.
    const reason = error instanceof Error ? error.message : "unknown error";
    return Response.json(
      {
        error: "recovery_engine_unreachable",
        message: `Could not reach the recovery engine at ${origin}: ${reason}`,
        hint: "Check API_ORIGIN and that the API container is healthy.",
      },
      { status: 502 },
    );
  }
}

export async function GET(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}

export async function POST(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}

export async function PUT(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}

export async function PATCH(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}

export async function DELETE(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}
