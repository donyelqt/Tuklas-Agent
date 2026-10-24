import { architecture } from "@/lib/copy";

/**
 * The compiled graph, drawn as a horizontal pipeline rail.
 *
 * Nine stations in a row, one per compiled graph node, connected by arrows
 * the way an engineer sketches the pipeline on a whiteboard. The main chain
 * runs left to right; the one retry node (broaden_search) sits in the chain
 * where the edge table puts it, and the two edges that skip over a neighbour
 * are routed as brackets above and below the row.
 *
 * Everything here is derived from `architecture.workflow.edges` and
 * `architecture.layerOf` at render time, so the drawing cannot drift from
 * apps/api/agent/graph.py: the station order, the connectors, and which edges
 * are flow vs retry all come from those two tables.
 *
 * No client code: this is a server component with no hooks, so the rail costs
 * no JavaScript on the marketing route. The rail is decorative (aria-hidden);
 * the node descriptions stay accessible in the shared section ledger. Radius 0,
 * no shadows, no em dash or en dash in any rendered string.
 */

type GraphEdge = { from: string; to: string; kind: "flow" | "retry" };

/**
 * Walk the edge table into a station order.
 *
 * The chain starts at the node nothing points at (the plan) and follows flow
 * edges to the end. Where a node also carries a retry edge, the retry target
 * is inserted immediately after its source so the back-edge reads as a side
 * station rather than a jump across the rail. Deterministic and derived, so
 * it tracks graph.py without drift.
 */
function deriveStationOrder(edges: GraphEdge[]): string[] {
  const flowEdges = edges.filter((e) => e.kind === "flow");
  const retryEdges = edges.filter((e) => e.kind === "retry");

  const flowTargets = new Set(flowEdges.map((e) => e.to));
  const start = flowEdges.find((e) => !flowTargets.has(e.from));
  if (!start) return [];

  const chain: string[] = [start.from];
  let cursor = start.from;
  let next = flowEdges.find((e) => e.from === cursor);
  while (next) {
    chain.push(next.to);
    cursor = next.to;
    next = flowEdges.find((e) => e.from === cursor);
  }

  const ordered: string[] = [];
  const visited = new Set<string>();
  for (const node of chain) {
    if (visited.has(node)) continue;
    visited.add(node);
    ordered.push(node);
    const retry = retryEdges.find((e) => e.from === node);
    if (retry && !visited.has(retry.to)) {
      visited.add(retry.to);
      ordered.push(retry.to);
    }
  }
  return ordered;
}

function StationCard({
  index,
  name,
  layerId,
  isSide,
  branchLabel,
}: {
  index: number;
  name: string;
  layerId: string;
  isSide: boolean;
  branchLabel: string;
}): React.JSX.Element {
  return (
    <div
      className={`flex w-full flex-col gap-1 border bg-ink-1 px-3 py-2 ${
        isSide ? "border-accent" : "border-ink-3"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <code className="font-mono text-[10px] tracking-[0.14em] text-ink-5">
          {String(index + 1).padStart(2, "0")}
        </code>
        {isSide ? (
          <code className="font-mono text-[9px] tracking-[0.14em] text-accent">
            {branchLabel}
          </code>
        ) : null}
      </div>
      <code
        className={`font-mono text-sm leading-tight break-words ${
          isSide ? "text-accent" : "text-ink-9"
        }`}
      >
        {name}
      </code>
      <code className="font-mono text-[10px] tracking-[0.12em] text-accent">
        {layerId}
      </code>
    </div>
  );
}

function Arrow({ kind }: { kind: "flow" | "retry" }): React.JSX.Element {
  return (
    <svg
      aria-hidden="true"
      className={`h-3 w-5 shrink-0 ${
        kind === "retry" ? "text-accent" : "text-ink-5"
      }`}
      viewBox="0 0 20 12"
      fill="none"
    >
      <line
        x1="0"
        y1="6"
        x2="14"
        y2="6"
        stroke="currentColor"
        strokeWidth={kind === "retry" ? 2 : 1}
      />
      <polygon points="14,1 20,6 14,11" fill="currentColor" />
    </svg>
  );
}

function BracketEdge({
  edge,
  fromIndex,
  toIndex,
}: {
  edge: GraphEdge;
  fromIndex: number;
  toIndex: number;
}): React.JSX.Element {
  const isRetry = edge.kind === "retry";
  const goingRight = toIndex > fromIndex;
  return (
    <svg
      aria-hidden="true"
      className={`h-4 w-6 shrink-0 ${
        isRetry ? "text-accent" : "text-ink-5"
      }`}
      viewBox="0 0 24 16"
      fill="none"
    >
      {goingRight ? (
        <>
          <line x1="5" y1="2" x2="5" y2="12" stroke="currentColor" strokeWidth={isRetry ? 2 : 1} />
          <line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" strokeWidth={isRetry ? 2 : 1} />
          <polygon points="19,6 23,10 19,14" fill="currentColor" />
        </>
      ) : (
        <>
          <line x1="19" y1="2" x2="19" y2="12" stroke="currentColor" strokeWidth={isRetry ? 2 : 1} />
          <line x1="19" y1="12" x2="5" y2="12" stroke="currentColor" strokeWidth={isRetry ? 2 : 1} />
          <polygon points="5,6 1,10 5,14" fill="currentColor" />
        </>
      )}
    </svg>
  );
}

export function ArchitecturePipelineRail(): React.JSX.Element {
  const { workflow, layerOf } = architecture;
  const { edges, steps, branchLabel } = workflow;

  const stations = deriveStationOrder(edges);
  const sideNode = steps.find((s) => s.branch)?.name;

  return (
    <div
      data-testid="architecture-pipeline-rail"
      data-variant="pipeline-rail"
      aria-hidden="true"
      className="w-full min-w-0"
    >
      <div className="overflow-x-auto">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-3">
          {stations.map((node, i) => {
            const next = stations[i + 1];
            const adjacentEdge = next
              ? edges.find(
                  (e) => e.from === node && e.to === next,
                )
              : undefined;
            // Edges from this node that do NOT point at the immediately
            // following station are routed as brackets instead of inline
            // arrows.
            const skipEdges = edges.filter(
              (e) =>
                e.from === node &&
                !(adjacentEdge && e.to === next),
            );

            return (
              <div
                key={node}
                className="flex min-w-[6rem] flex-1 flex-row items-center gap-x-2"
              >
                <div className="flex-1 min-w-0">
                  <StationCard
                    index={i}
                    name={node}
                    layerId={layerOf[node]}
                    isSide={node === sideNode}
                    branchLabel={branchLabel}
                  />
                </div>
                {skipEdges.map((edge) => (
                  <BracketEdge
                    key={edge.from + ">" + edge.to}
                    edge={edge}
                    fromIndex={i}
                    toIndex={stations.indexOf(edge.to)}
                  />
                ))}
                {adjacentEdge ? (
                  <Arrow kind={adjacentEdge.kind} />
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
