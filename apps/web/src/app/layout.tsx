import type { Metadata, Viewport } from "next";
import { Geist, JetBrains_Mono } from "next/font/google";
import type { ReactNode } from "react";
import { site } from "@/lib/site";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(site.repositoryUrl),
  title: {
    default: site.name,
    template: `%s | ${site.name}`,
  },
  description: site.tagline,
  robots: {
    index: true,
    follow: true,
  },
  openGraph: {
    type: "website",
    siteName: site.name,
    title: site.name,
    description: site.tagline,
    url: "/",
    // TODO: the /og.png social card asset has not been produced yet.
    images: ["/og.png"],
  },
  twitter: {
    card: "summary_large_image",
    title: site.name,
    description: site.tagline,
    images: ["/og.png"],
  },
};

export const viewport: Viewport = {
  themeColor: "#050507",
};

/**
 * The root layout's props, declared inline rather than with Next's generated
 * `LayoutProps<"/">` global.
 *
 * `LayoutProps` is emitted into `.next/types/routes.d.ts` by `next build` and
 * `next dev`, and `next-env.d.ts` imports that generated file. `.next/` is a
 * build artifact and `next-env.d.ts` is gitignored, so on a fresh checkout the
 * global is not declared anywhere and `tsc` fails with TS2304. The CI gate
 * runs `tsc` before any build, so it cannot depend on a build artifact.
 *
 * The shape below is the one Next generates for a root layout with no
 * dynamic segments and no named slots: `params` is a promise resolving to the
 * empty param map, and `children` is the React node. Declaring it here keeps
 * the same contract without the build-order dependency, and it stays
 * assignable to the generated type Next validates against at build time.
 */
type RootLayoutProps = Readonly<{
  children: ReactNode;
  params: Promise<Record<string, never>>;
}>;

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${jetbrainsMono.variable} h-full min-h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-ink-0 text-ink-9">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:bg-ink-1 focus:px-4 focus:py-2 focus:text-ink-9 focus:outline-2 focus:outline-accent"
        >
          Skip to content
        </a>
        <main id="main" className="flex-1">
          {children}
        </main>
      </body>
    </html>
  );
}