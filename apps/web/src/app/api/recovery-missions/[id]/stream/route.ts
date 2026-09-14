import type { NextRequest } from "next/server";

/**
 * Streams mission events straight through from FastAPI.
 *
 * The generic /api/* rewrite cannot serve this endpoint: Next compresses proxied
 * responses when the client advertises gzip — which every browser does — and the
 * gzip encoder buffers, so an SSE stream that works perfectly under curl (no
 * Accept-Encoding by default) delivers nothing at all to a real browser.
 *
 * This handler takes precedence over the rewrite and pins the response to
 * identity encoding, end to end.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const fetchCache = "force-no-store";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const since = request.nextUrl.searchParams.get("since") ?? "0";

  const upstream = await fetch(
    `${API_ORIGIN}/api/recovery-missions/${encodeURIComponent(id)}/stream?since=${since}`,
    {
      headers: { Accept: "text/event-stream", "Accept-Encoding": "identity" },
      signal: request.signal,
      cache: "no-store",
    },
  );

  if (!upstream.ok || !upstream.body) {
    return new Response(`Mission stream unavailable (${upstream.status})`, {
      status: upstream.status,
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      // no-transform additionally forbids any intermediary from re-compressing.
      "Cache-Control": "no-cache, no-transform",
      "Content-Encoding": "identity",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
