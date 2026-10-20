import type { Metadata } from "next";
import { Explorer } from "@/components/explorer/explorer";
import { explorer } from "@/lib/explorer-copy";

export const metadata: Metadata = {
  title: explorer.meta.title,
  description: explorer.meta.description,
  robots: {
    // The explorer is a local runtime against a local API. It has no
    // content for a crawler, and indexing it would publish a document
    // browser that can reach nothing.
    index: false,
    follow: false,
  },
};

/**
 * The document explorer route (architecture.md section 7, PRD F6).
 *
 * The page itself is a thin shell: everything interactive lives in the
 * client component, because the explorer is a browser-side runtime that
 * calls the local API from the browser. That is also what lets this route
 * be a static export.
 */
export default function ExplorerPage() {
  return <Explorer />;
}
