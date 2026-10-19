import type { NextConfig } from "next";

const nextConfig: NextConfig = {
   /* Static export: the landing page deploys to Vercel as a zero-runtime
      static build. The /app/* explorer is a separate runtime that calls a
      local API, so it is excluded from the marketing export. */
   output: "export",
   images: { unoptimized: true },
   /* API base URL: environment-driven. Default is localhost — this is a
      local-first tool. The landing page makes no API calls; only the
      /app/* explorer consumes the API. */
   env: {
     NEXT_PUBLIC_API_BASE_URL:
       process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000",
   },
   /* PPR (cacheComponents/partialPrefetching) is incompatible with output:
      "export" and is omitted. The landing page is a pure static build. */
};

export default nextConfig;
