import { architecture } from "@/lib/copy";

/**
 * The compiled graph, drawn as an SVG node graph.
 *
 * Three rows over a 1000×600 viewBox:
 *   Row 1 (y=5%):  query_plan, search, inspect, expand_or_backtrack, select_evidence
 *   Row 2 (y=43%): broaden_search (left), sufficiency_gate (right)
 *   Row 3 (y=81%): generate (left), build_context (right)
 *
 * The nine edges come from `architecture.workflow.edges`. The two retry
 * edges (kind === "retry") are magenta; every flow edge is decoration.
 * Each node box shows its name and owning layer id from `architecture.layerOf`.
 *
 * No client code: pure server component, no hooks, no observers.
 */

const NODE_W = 16.8; // percent of viewBox width
const NODE_H = 9; // percent of viewBox height

// Position each node by its step index, matching the prototype's three-row layout.
function pos(i: number): { x: number; y: number } {
  if (i <= 4) return { x: 2 + i * 19.6, y: 5 };
  if (i === 5) return { x: 80.4, y: 43 };
  if (i === 6) return { x: 2, y: 43 };
  if (i === 7) return { x: 80.4, y: 81 };
  return { x: 60.8, y: 81 };
}

interface Bounds {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  cx: number;
  cy: number;
}

// Convert percentage positions to viewBox pixel coordinates (0-1000, 0-600).
function nodeBounds(i: number): Bounds {
  const p = pos(i);
  const x1 = p.x * 10;
  const y1 = p.y * 6;
  return {
    x1,
    y1,
    x2: x1 + NODE_W * 10,
    y2: y1 + NODE_H * 6,
    cx: x1 + NODE_W * 5,
    cy: y1 + NODE_H * 3,
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
            preserveAspectRatio="none"
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
            return (
              <div
                key={step.name}
                className="absolute border border-ink-4 bg-ink-2 px-2 py-1.5"
                style={{
                  left: `${p.x}%`,
                  top: `${p.y}%`,
                  width: `${NODE_W}%`,
                  height: `${NODE_H}%`,
                }}
              >
                <div className="font-mono text-[10px] leading-tight text-ink-9">
                  {step.name}
                </div>
                <div className="font-mono text-[9px] leading-tight text-ink-7">
                  {layerId}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
