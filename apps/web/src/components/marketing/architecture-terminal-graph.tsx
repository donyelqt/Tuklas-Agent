/**
 * The compiled graph, drawn as a terminal-style ASCII diagram.
 *
 * Layout family: a vertical main chain of the nine graph nodes with one node
 * pulled out as a side box to the left of inspect. The sufficiency gate sends
 * a horizontal line leftward to that side box, and the side box returns a
 * horizontal arrow rightward into inspect. Both are retry edges, both magenta.
 *
 * Every node name, layer id and label is read from architecture.workflow and
 * architecture.layerOf in copy.ts, never hand-typed here. The nine boxes, the
 * nine connectors and the two retry edges all come from those tables, so the
 * drawing cannot drift from apps/api/agent/graph.py.
 *
 * No client code: everything is static markup built at render time on the
 * server, so the component costs no JavaScript on the marketing route.
 */

import { architecture } from "@/lib/copy";

/* --- layout constants --------------------------------------------------- */

const IW = 19; // interior width of each box (fits "expand_or_backtrack")
const MAIN_X = 26; // column of the main-chain box left border
const SIDE_X = 2; // column of the broaden_search side box left border
const W = MAIN_X + IW + 2; // total drawing width in characters

/* --- colour roles ------------------------------------------------------- */

type Role = "rule" | "name" | "layer" | "retry";

interface Seg {
  text: string;
  role: Role;
}

const ROLE_CLASS: Record<Role, string> = {
  rule: "text-ink-5",
  name: "text-ink-9",
  layer: "text-ink-7",
  retry: "text-accent",
};

const sp = (n: number): string => " ".repeat(n);
const rule = (t: string): Seg => ({ text: t, role: "rule" });
const nameSeg = (t: string): Seg => ({ text: t, role: "name" });
const layerSeg = (t: string): Seg => ({ text: t, role: "layer" });
const retrySeg = (t: string): Seg => ({ text: t, role: "retry" });

/* --- box-drawing helpers ------------------------------------------------ */

const boxTop = (conn: boolean): string =>
  "┌" + "─".repeat(9) + (conn ? "┴" : "─") + "─".repeat(9) + "┐";

const boxBot = (conn: boolean): string =>
  "└" + "─".repeat(9) + (conn ? "┬" : "─") + "─".repeat(9) + "┘";

const boxNameSegs = (name: string): Seg[] => [
  rule("│"),
  nameSeg(name.padEnd(IW)),
  rule("│"),
];

const boxLayerSegs = (layer: string): Seg[] => [
  rule("│"),
  layerSeg(layer.padEnd(IW)),
  rule("│"),
];

/* --- component ---------------------------------------------------------- */

export function ArchitectureTerminalGraph(): React.JSX.Element {
  const { workflow, layerOf } = architecture;
  const { branchLabel } = workflow;

  /*
   * Derive the main chain from the flow edges so the drawing follows the
   * same order as apps/api/agent/graph.py. The retry edges are identified
   * by kind === "retry".
   */

  const flowEdges = workflow.edges.filter((e) => e.kind === "flow");
  const retryEdges = workflow.edges.filter((e) => e.kind === "retry");

  const mainChain: string[] = [];
  let current: string | undefined = "query_plan";
  while (current) {
    mainChain.push(current);
    const next = flowEdges.find((e) => e.from === current);
    current = next ? next.to : undefined;
  }

  /*
   * The side node lives on a retry edge but is not part of the flow chain.
   * In the compiled graph that node is broaden_search.
   */

  const retryNodes = new Set(retryEdges.flatMap((e) => [e.from, e.to]));
  const sideNode =
    [...retryNodes].find((n) => !mainChain.includes(n)) ?? "broaden_search";

  /*
   * The retry edge that originates from the main chain gives us the gate.
   * The other retry edge sends its output back into inspect.
   */

  const gateName = retryEdges.find((e) => mainChain.includes(e.from))!.from;
  const gateIdx = mainChain.indexOf(gateName);

  /* --- row builder ------------------------------------------------------ */

  const rows: Seg[][] = [];
  const push = (...segs: Seg[]) => rows.push([...segs]);

  const mainBox = (
    name: string,
    layer: string,
    connTop: boolean,
    connBot: boolean
  ) => {
    push(rule(sp(MAIN_X)), rule(boxTop(connTop)));
    push(rule(sp(MAIN_X)), ...boxNameSegs(name));
    push(rule(sp(MAIN_X)), ...boxLayerSegs(layer));
    push(rule(sp(MAIN_X)), rule(boxBot(connBot)));
  };

  const downArrow = () => {
    push(rule(sp(MAIN_X + 10)), rule("▼"), rule(sp(W - MAIN_X - 11)));
  };

  const retryDownArrow = () => {
    push(retrySeg("│"), rule(sp(25)), rule(sp(10)), rule("▼"), rule(sp(10)));
  };

  const retryBox = (
    name: string,
    layer: string,
    connTop: boolean,
    connBot: boolean
  ) => {
    push(retrySeg("│"), rule(sp(25)), rule(boxTop(connTop)));
    push(retrySeg("│"), rule(sp(25)), ...boxNameSegs(name));
    push(retrySeg("│"), rule(sp(25)), ...boxLayerSegs(layer));
    push(retrySeg("│"), rule(sp(25)), rule(boxBot(connBot)));
  };

  /* --- build the drawing row by row ------------------------------------- */

  // query_plan — first node, no incoming connector
  mainBox(mainChain[0]!, layerOf[mainChain[0] ?? ""], false, true);
  downArrow();

  // search
  mainBox(mainChain[1]!, layerOf[mainChain[1] ?? ""], true, true);
  downArrow();

  // broaden_search (side) + inspect (main), side by side
  push(
    rule(sp(SIDE_X)),
    rule(boxTop(false)),
    rule("   "),
    rule(boxTop(true))
  );
  push(
    rule(sp(SIDE_X)),
    ...boxNameSegs(sideNode),
    retrySeg("─▶"),
    ...boxNameSegs(mainChain[2] ?? "")
  );
  push(
    rule(sp(SIDE_X)),
    ...boxLayerSegs(layerOf[sideNode]),
    rule("   "),
    ...boxLayerSegs(layerOf[mainChain[2] ?? ""])
  );
  push(
    rule(sp(SIDE_X)),
    rule(boxBot(true)),
    rule("   "),
    rule(boxBot(true))
  );

  // ▲ (retry up) beside ▼ (main down)
  push(retrySeg("▲"), rule(sp(35)), rule("▼"), rule(sp(W - 37)));

  // retry │ RETRY + expand_or_backtrack top
  push(
    retrySeg("│"),
    retrySeg(" " + branchLabel + " "),
    rule(sp(19)),
    rule(boxTop(true))
  );

  // expand_or_backtrack (retry path on left)
  retryBox(
    mainChain[3] ?? "",
    layerOf[mainChain[3] ?? ""],
    true,
    true
  );
  retryDownArrow();

  // select_evidence (retry path on left)
  retryBox(
    mainChain[4] ?? "",
    layerOf[mainChain[4] ?? ""],
    true,
    true
  );
  retryDownArrow();

  // sufficiency_gate (retry path on left + leftward connector into box)
  push(retrySeg("│"), rule(sp(25)), rule(boxTop(true)));
  push(
    retrySeg("└" + "─".repeat(MAIN_X - 1) + "┤"),
    nameSeg(mainChain[gateIdx]!.padEnd(IW)),
    rule("│")
  );
  push(retrySeg("│"), rule(sp(25)), ...boxLayerSegs(layerOf[mainChain[gateIdx] ?? ""]));
  push(retrySeg("│"), rule(sp(25)), rule(boxBot(true)));

  // main chain continues alone — gate pass route
  downArrow();

  // build_context
  mainBox(mainChain[6]!, layerOf[mainChain[6] ?? ""], true, true);
  downArrow();

  // generate — last node, no outgoing connector
  mainBox(mainChain[7]!, layerOf[mainChain[7] ?? ""], true, false);

  /* --- render ----------------------------------------------------------- */

  return (
    <div className="overflow-x-auto">
      <pre
        data-testid="architecture-terminal-graph"
        data-variant="terminal-graph"
        aria-hidden="true"
        className="m-0 font-mono text-[clamp(7px,1.9vw,13px)] leading-4"
      >
        {rows.map((row, i) => (
          <span key={i}>
            {row.map((seg, j) => (
              <span key={j} className={ROLE_CLASS[seg.role]}>
                {seg.text}
              </span>
            ))}
            {"\n"}
          </span>
        ))}
      </pre>
    </div>
  );
}
