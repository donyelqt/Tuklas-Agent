/**
 * Serve the static export.
 *
 * `next build` with `output: "export"` writes plain files into `out/`.
 * `next start` refuses to serve that configuration - it says so and
 * exits - so the `start` script needs a server of its own.
 *
 * This is that server, and it is deliberately dependency-free: the
 * export is a directory of files, and serving a directory of files does
 * not need a framework.
 *
 * Two details the explorer needs and a naive static server gets wrong:
 *
 *  * The export writes `app.html` for the `/app` route. A browser that
 *    asks for `/app` expects that file, so a request for a path with no
 *    extension falls back to `<path>.html`, and a request for a
 *    directory falls back to `<path>/index.html`.
 *  * The `_next/` assets are content-hashed, so they are safe to cache
 *    forever. The HTML is not, so it is served with no-store.
 *
 * Usage:
 *
 *     node scripts/serve-export.mjs [--port 3000] [--dir out]
 */

import { createServer } from "node:http";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const args = process.argv.slice(2);
const port = Number(readFlag("--port") ?? 3000);
const host = readFlag("--host") ?? "127.0.0.1";
const root = resolve(
  fileURLToPath(new URL("..", import.meta.url)),
  readFlag("--dir") ?? "out",
);

function readFlag(name) {
  const index = args.indexOf(name);
  return index === -1 ? undefined : args[index + 1];
}

const CONTENT_TYPES = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".webp": "image/webp",
  ".woff2": "font/woff2",
};

/**
 * Resolve a request path to a file inside the export.
 *
 * Returns null when the path escapes the export directory. The check
 * matters even for a local server: a path like `/../../etc/passwd`
 * normalises to something outside `root`, and this is the line that
 * refuses it.
 */
async function resolveFile(pathname) {
  const decoded = decodeURIComponent(pathname);
  const candidates = [];
  const relative = normalize(decoded).replace(/^([/\\])+/, "");
  const base = join(root, relative);
  if (base !== root && !base.startsWith(root + sep)) {
    return null;
  }
  candidates.push(base);
  if (!extname(base)) {
    candidates.push(`${base}.html`, join(base, "index.html"));
  }
  for (const candidate of candidates) {
    try {
      const info = await stat(candidate);
      if (info.isFile()) {
        return candidate;
      }
    } catch {
      // Try the next candidate.
    }
  }
  return null;
}

const server = createServer(async (request, response) => {
  const { pathname } = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
  const file = await resolveFile(pathname);
  if (file === null) {
    const notFound = join(root, "404.html");
    try {
      await stat(notFound);
      response.writeHead(404, { "content-type": CONTENT_TYPES[".html"] });
      createReadStream(notFound).pipe(response);
      return;
    } catch {
      response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      response.end("Not found\n");
      return;
    }
  }
  const type = CONTENT_TYPES[extname(file)] ?? "application/octet-stream";
  const immutable = file.includes(`${sep}_next${sep}`);
  response.writeHead(200, {
    "content-type": type,
    "cache-control": immutable ? "public, max-age=31536000, immutable" : "no-store",
  });
  createReadStream(file).pipe(response);
});

server.listen(port, host, () => {
  console.log(`serving ${root}`);
  console.log(`export serve on ${port}`);
});
