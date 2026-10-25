import { architecture } from "@/lib/copy";

/**
 * The compiled graph, drawn as an SVG node graph.
 *
 * Three rows over a 1000x600 canvas, positioned by step index to match the
 * prototype. The nine edges come from `architecture.workflow.edges`: the two
 * retry edges (kind === "retry") are magenta, every flow edge is decoration.
 *
 * Each node carries its index, name, owning layer id from
 * `architecture.layerOf`. This view alone carries a second, narrower control
 * that switches between two layouts: the described layout draws
 * `workflow.steps[].body` under each name, the name-only layout drops the
 * bodies and shrinks the boxes so the diagram reads as a topology again. The
 * control lives inside this view's panel, so it is only on screen in the view
 * it belongs to.
 *
 * The two layouts are two renders of the same nine boxes and the same nine
 * edges, and the switch crossfades them. It does not animate height or top:
 * DESIGN.md section 6 bans animating those repo-wide, and height animation
 * triggers layout on every frame. Opacity and transform are compositor-only,
 * and the global reduced-motion block cuts the duration to 0.01ms, so a
 * reader who asks for less motion gets an instant swap.
 *
 * One box in the view is the retry node, marked with `branch: true`. It
 * carries a magenta border and a RETRY label in both layouts, so the cycle
 * reads without inferring it from spacing, and in the described layout it is
 * drawn taller because its body is the longest of the nine and must not clip.
 *
 * No client code: pure server component, no hooks, no observers.
 */

const NODE_W = 16.8; // percent of canvas width
const NODE_H_DESCRIBED = 21; // percent of canvas height; carries name and body
const NODE_H_COMPACT = 11; // percent of canvas height; index, name and layer only
const RETRY_EXTRA_H = 6; // the retry node's body is longer, so its box is taller

/*
 * Both layouts are placed on the same canvas grid, and the row positions are
 * chosen so each layout's tallest box still ends inside the canvas. The
 * described layout's tallest box is 27 units (21 + 6); the compact layout's
 * tallest is 17 units. Row three at 78% clears both.
 */
const ROW = [
  { x: 2, y: 1 },
  { x: 21.6, y: 1 },
  { x: 40.8, y: 1 },
  { x: 60.4, y: 1 },
  { x: 79.6, y: 1 },
  { x: 80.4, y: 40 },
  { x: 2, y: 40 },
  { x: 80.4, y: 78 },
  { x: 60.8, y: 78 },
];

const heightFor = (i: number, compact: boolean): number => {
  const step = architecture.workflow.steps[i];
  const extra = step?.branch === true ? RETRY_EXTRA_H : 0;
  const base = compact ? NODE_H_COMPACT : NODE_H_DESCRIBED;
  return base + extra;
};

/* Edge routing uses the described geometry, which is the layout the prototype
 * matches. Its coordinates feed both renders, so the two layouts share one
 * edge model and cannot disagree about which node an edge connects. */

interface Bounds {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  cx: number;
  cy: number;
}

const nodeBounds = (i: number): Bounds => {
  const p = ROW[i]!;
  const h = heightFor(i, false);
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
};

// Derive an SVG path between two nodes from their canvas coordinates.
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

/*
 * Both renders are stacked in the same cell. The hidden one is never removed,
 * only faded and nudged 1px down, so the swap reads as a crossfade rather
 * than a pop, and the container never collapses between states. The 150ms
 * base is DESIGN.md section 6's base transition; the reduced-motion block
 * shortens it for readers who ask.
 */
const DETAIL_CSS = `
  [data-node-detail-panels] { display: grid; }
  [data-node-detail-panels] > * { grid-area: 1 / 1; }
  [data-node-detail="off"] {
    opacity: 0;
    transform: translateY(1px);
    transition: opacity 150ms cubic-bezier(0.4, 0, 0.2, 1),
      transform 150ms cubic-bezier(0.4, 0, 0.2, 1);
    pointer-events: none;
  }
  #node-detail-off:checked ~ [data-node-detail-panels] > [data-node-detail="on"] {
    opacity: 0;
    transform: translateY(1px);
    pointer-events: none;
  }
  #node-detail-off:checked ~ [data-node-detail-panels] > [data-node-detail="off"] {
    opacity: 1;
    transform: translateY(0);
  }
  [data-node-detail="on"] {
    opacity: 1;
    transform: translateY(0);
    transition: opacity 150ms cubic-bezier(0.4, 0, 0.2, 1),
      transform 150ms cubic-bezier(0.4, 0, 0.2, 1);
  }
  #node-detail-on:checked ~ [role="radiogroup"] label[for="node-detail-on"],
  #node-detail-off:checked ~ [role="radiogroup"] label[for="node-detail-off"] {
    color: var(--color-ink-7);
    border-bottom-color: var(--color-accent);
  }
`;

/* One node box. `compact` drops the body row and draws the shorter box, so the
 * name-only layout reads as nine chips rather than nine boxes with holes. */
function NodeBoxes({
  steps,
  layerOf,
  compact,
}: {
  steps: typeof architecture.workflow.steps;
  layerOf: Record<string, string>;
  compact: boolean;
}): React.JSX.Element {
  return (
    <>
      {steps.map((step, i) => {
        const p = ROW[i]!;
        const layerId = layerOf[step.name] ?? "";
        const isRetry = step.branch === true;
        return (
          <div
            key={step.name}
            className={`absolute bg-ink-2 px-2 ${
              compact ? "py-1" : "py-1.5"
            } ${isRetry ? "border border-accent" : "border border-ink-4"}`}
            style={{
              left: `${p.x}%`,
              top: `${p.y}%`,
              width: `${NODE_W}%`,
              height: `${heightFor(i, compact)}%`,
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
              className={`font-mono text-[10px] leading-tight ${
                isRetry ? "text-accent" : "text-ink-9"
              }`}
            >
              {step.name}
            </div>
            <div className="font-mono text-[9px] leading-tight text-ink-7">
              {layerId}
            </div>
            {compact ? null : (
              <div
                className={`mt-1 text-[9px] leading-[1.3] text-ink-7 ${
                  isRetry ? "line-clamp-6" : "line-clamp-4"
                }`}
              >
                {step.body}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

function Edges(): React.JSX.Element {
  const steps = architecture.workflow.steps;
  const edges = architecture.workflow.edges;
  return (
    <>
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
          <path d="M0 0 L10 5 L0 10 Z" fill="var(--color-ink-4)" />
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
          <path d="M0 0 L10 5 L0 10 Z" fill="var(--color-accent)" />
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
            stroke={isRetry ? "var(--color-accent)" : "var(--color-ink-4)"}
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
            markerEnd={isRetry ? "url(#node-graph-arrow-retry)" : "url(#node-graph-arrow)"}
          />
        );
      })}
    </>
  );
}

export function ArchitectureNodeGraph(): React.JSX.Element {
  const steps = architecture.workflow.steps;
  const layerOf = architecture.layerOf;
  const nodeDetail = architecture.nodeDetail;

  return (
    <div
      data-testid="architecture-node-graph"
      data-variant="node-graph"
      className="overflow-x-hidden"
    >
      <div data-node-detail-view className="relative isolate flex flex-col">
        <style dangerouslySetInnerHTML={{ __html: DETAIL_CSS }} />

        {/* The control. Hidden radios come first as siblings of both the
            labels and the graph, so the CSS above can reach either. */}
        <input
          type="radio"
          id="node-detail-on"
          name="node-detail"
          value="on"
          aria-label={nodeDetail.on}
          defaultChecked
          className="sr-only"
        />
        <input
          type="radio"
          id="node-detail-off"
          name="node-detail"
          value="off"
          aria-label={nodeDetail.off}
          className="sr-only"
        />
        <div
          aria-label={nodeDetail.caption}
          role="radiogroup"
          tabIndex={-1}
          className="mb-3 inline-flex items-center gap-3 self-start"
        >
          <label
            htmlFor="node-detail-on"
            data-testid="node-detail-label-on"
            className="cursor-pointer border-b-2 border-transparent px-1 py-1 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-9 transition-colors duration-150 hover:text-ink-7"
          >
            {nodeDetail.on}
          </label>
          <label
            htmlFor="node-detail-off"
            data-testid="node-detail-label-off"
            className="cursor-pointer border-b-2 border-transparent px-1 py-1 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-9 transition-colors duration-150 hover:text-ink-7"
          >
            {nodeDetail.off}
          </label>
        </div>

        <div data-node-detail-panels className="overflow-x-auto">
          <div data-node-detail="on" className="relative h-full">
            <div className="relative" style={{ aspectRatio: "1000/600" }}>
              <svg
                viewBox="0 0 1000 600"
                preserveAspectRatio="xMidYMid meet"
                aria-hidden="true"
                className="absolute inset-0 h-full w-full"
              >
                <Edges />
              </svg>
              <NodeBoxes steps={steps} layerOf={layerOf} compact={false} />
            </div>
          </div>

          <div data-node-detail="off" className="relative h-full">
            <div className="relative" style={{ aspectRatio: "1000/600" }}>
              <svg
                viewBox="0 0 1000 600"
                preserveAspectRatio="xMidYMid meet"
                aria-hidden="true"
                className="absolute inset-0 h-full w-full"
              >
                <Edges />
              </svg>
              <NodeBoxes steps={steps} layerOf={layerOf} compact />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
