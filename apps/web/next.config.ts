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
   /* Tailwind v4 is compiled by the Turbopack loader, NOT by PostCSS.
      There is no `postcss.config.mjs` in this package and there is no
      `@tailwindcss/postcss` dependency: the loader is the whole pipeline.
      Dropping it (as the static-export commit did) does not fail the
      build - it emits a stylesheet with the `@theme` custom properties and
      the `@layer base` rules but with NO utility classes, so every page
      renders unstyled while `next build` still reports success. The
      loader is what turns `class="flex min-h-0"` into CSS. */
   turbopack: {
     rules: {
       "*.css": {
         loaders: ["@tailwindcss/turbopack"],
         as: "*.css",
       },
     },
   },
};

export default nextConfig;
