import { NextRequest, NextResponse } from "next/server";

const upstream = process.env.ZHOUXU_API_URL ?? "http://127.0.0.1:8010";

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const url = new URL(path.map(encodeURIComponent).join("/"), `${upstream.replace(/\/$/, "")}/`);
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.append(key, value));

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  const authorization = request.headers.get("authorization");
  if (contentType) headers.set("content-type", contentType);
  if (authorization) headers.set("authorization", authorization);

  try {
    const response = await fetch(url, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      cache: "no-store",
      signal: request.signal,
    });
    const responseHeaders = new Headers();
    const responseType = response.headers.get("content-type");
    if (responseType) responseHeaders.set("content-type", responseType);
    return new NextResponse(response.body, { status: response.status, headers: responseHeaders });
  } catch {
    return NextResponse.json({ error: "service_unavailable" }, { status: 503 });
  }
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const DELETE = forward;
