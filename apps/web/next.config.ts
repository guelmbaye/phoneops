import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Emits .next/standalone so the container ships the server and only the
  // dependencies it actually traced, instead of the whole node_modules tree.
  output: "standalone",

  // No `rewrites()` here on purpose. Next compiles rewrite destinations into
  // routes-manifest.json at build time, so an image built without API_ORIGIN
  // froze the proxy target as http://localhost:8000 and ignored the variable
  // supplied at runtime. The proxy lives in src/app/api/[...path]/route.ts,
  // which reads the environment per request.
};

export default nextConfig;
