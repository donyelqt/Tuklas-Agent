import { architecture } from "@/lib/copy";

/**
 * The compiled graph, drawn as an SVG node graph.
 *
 * Three rows over a 1000x600 viewBox, positioned by step index to match the
 * prototype. The nine edges come from `architecture.workflow.edges`: the two
 * retry edges (kind === "retry") are magenta, every flow edge is decoration.
 *
 * Each node box carries its index, name, owning layer id from
 * `architecture.layerOf`, and body text from `workflow.steps`. The one node
 * the retry loop passes through (`branch: true`) takes a magenta border and a
 * RETRY label, so the retry path reads without inferring it from spacing, per
 * DESIGN.md section 7.
 *
 * No client code: pure server component, no hooks, no observers.
 */

const NODE_W = 16.8; // percent of viewBox width
const NODE_H = 21; // percent of viewBox height; carries index, name and body
const RETRY_EXTRA_H = 6; // the retry node's body is longer, so its box is taller

/* Three rows over the 1000x600 canvas, spaced so a 21%-tall box ends at 99%
 * and the rows read as a graph rather than a list with holes in it. */
function pos(i: number): { x: number; y: number } {
  if (i <= 4) return { x: 2 + i * 19.6, y: 1 };
  if (i === 5) return { x: 80.4, y: 40 };
  if (i === 6) return { x: 2, y: 40 };
  if (i === 7) return { x: 80.4, y: 78 };
  return { x: 60.8, y: 78 };
}

interface Bounds {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  cx: number;
  cy: number;
}

/* The retry node carries a longer body, so its box is taller. Edge routing
 * must know that, or a return edge aims at the wrong vertical point. */
const nodeHeight = (i: number): number => {
  const step = architecture.workflow.steps[i];
  return step?.branch === true ? NODE_H + RETRY_EXTRA_H : NODE_H;
};

// Convert percentage positions to viewBox pixel coordinates (0-1000, 0-600).
function nodeBounds(i: number): Bounds {
  const p = pos(i);
  const h = nodeHeight(i);
  const x1 = p.x * 10;
  const y1 = p.y * 6;
  return {
    x1,
    y1,
    x2: x1 + NODE_W * 10,
    y2: y1 + h * 6,
    cx: x1 + NODE_W * 5,
    cy: y1 + h * 3,
  };
}

// Derive an SVG path between two nodes from their viewBox coordinates.
function edgeD(from: number, to: number): string {
  const a = nodeBounds(from);
  const b = nodeBounds(to);
  const sameRow = Math.abs(a.cy - b.cy) < 1;
  const sameCol = Math.abs(a.cx - b.cx) < 1;

  if (sameRow) {
    return b.cx > a.cx
      ? `M${a.x2} ${a.cy} H${b.x1}`
      : `M${a.x1} ${a.cy} H${b.x2}`;
  }
  if (sameCol) {
    return b.cy > a.cy
      ? `M${a.cx} ${a.y2} V${b.y1}`
      : `M${a.cx} ${a.y1} V${b.y2}`;
  }
  // Cross-row, cross-column: L-shaped via a midpoint on the source side.
  const midY = (a.cy + b.cy) / 2;
  return b.cy > a.cy
    ? `M${a.cx} ${a.y2} V${midY} H${b.cx} V${b.y1}`
    : `M${a.cx} ${a.y1} V${midY} H${b.cx} V${b.y2}`;
}

export function ArchitectureNodeGraph(): React.JSX.Element {
  const steps = architecture.workflow.steps;
  const edges = architecture.workflow.edges;
  const layerOf = architecture.layerOf;

  return (
    <div
      data-testid="architecture-node-graph"
      data-variant="node-graph"
      className="overflow-x-hidden"
    >
      <div className="overflow-x-auto">
        <div className="relative" style={{ aspectRatio: "1000/600" }}>
          <svg
            viewBox="0 0 1000 600"
            preserveAspectRatio="xMidYMid meet"
            aria-hidden="true"
            className="absolute inset-0 h-full w-full"
          >
            <defs>
              <marker
                id="node-graph-arrow"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="9"
                markerHeight="9"
                orient="auto"
                markerUnits="userSpaceOnUse"
              >
                <path
                  d="M0 0 L10 5 L0 10 Z"
                  fill="var(--color-ink-4)"
                />
              </marker>
              <marker
                id="node-graph-arrow-retry"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="9"
                markerHeight="9"
                orient="auto"
                markerUnits="userSpaceOnUse"
              >
                <path
                  d="M0 0 L10 5 L0 10 Z"
                  fill="var(--color-accent)"
                />
              </marker>
            </defs>
            {edges.map((edge) => {
              const fi = steps.findIndex((s) => s.name === edge.from);
              const ti = steps.findIndex((s) => s.name === edge.to);
              const isRetry = edge.kind === "retry";
              return (
                <path
                  key={`${edge.from}-${edge.to}`}
                  d={edgeD(fi, ti)}
                  fill="none"
                  stroke={
                    isRetry ? "var(--color-accent)" : "var(--color-ink-4)"
                  }
                  strokeWidth="1.5"
                  vectorEffect="non-scaling-stroke"
                  markerEnd={
                    isRetry
                      ? "url(#node-graph-arrow-retry)"
                      : "url(#node-graph-arrow)"
                  }
                />
              );
            })}
          </svg>
          {steps.map((step, i) => {
            const p = pos(i);
            const layerId = layerOf[step.name];
            const isRetry = step.branch === true;
            return (
              <div
                key={step.name}
                className={`absolute overflow-hidden bg-ink-2 px-2 py-1.5 ${
                  isRetry ? "border border-accent" : "border border-ink-4"
                }`}
                style={{
                  left: `${p.x}%`,
                  top: `${p.y}%`,
                  width: `${NODE_W}%`,
                  height: isRetry ? `${NODE_H + 6}%` : `${NODE_H}%`,
                }}
              >
                <div className="flex items-baseline justify-between gap-1">
                  <div
                    className={`font-mono text-[9px] leading-none ${
                      isRetry ? "text-accent" : "text-ink-7"
                    }`}
                  >
                    {String(i + 1).padStart(2, "0")}
                  </div>
                  {isRetry ? (
                    <div className="font-mono text-[8px] uppercase tracking-[0.12em] leading-none text-accent">
                      RETRY
                    </div>
                  ) : null}
                </div>
                <div
                  className={`mt-1 font-mono text-[10px] leading-tight ${
                    isRetry ? "text-accent" : "text-ink-9"
                  }`}
                >
                  {step.name}
                </div>
                <div className="font-mono text-[9px] leading-tight text-ink-7">
                  {layerId}
                </div>
                <div
                  className={`mt-1 text-[9px] leading-[1.3] text-ink-7 ${
                    isRetry ? "line-clamp-5" : "line-clamp-4"
                  }`}
                >
                  {step.body}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
