import { NextRequest, NextResponse } from "next/server";

const BACKEND = "http://localhost:8000";
const PROXY_PREFIXES = ["/auth/", "/chatbot/", "/franklin/", "/api/"];

export default function proxy(req: NextRequest) {
  const path = req.nextUrl.pathname;
  if (PROXY_PREFIXES.some((p) => path.startsWith(p))) {
    const target = new URL(path + req.nextUrl.search, BACKEND);
    return NextResponse.rewrite(target);
  }
}

export const config = {
  matcher: [
    "/auth/:path*",
    "/chatbot/:path*",
    "/franklin/:path*",
    "/api/:path*",
  ],
};
