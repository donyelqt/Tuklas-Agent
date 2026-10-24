import { architecture } from "@/lib/copy";

/**
 * The compiled graph, drawn as terminal box-drawing ASCII.
 *
 * The layout is DERIVED, not composed. Every node box, every connector and
 * both retry edges are placed by walking `architecture.workflow.edges` and
 * `architecture.layerOf`, so the drawing cannot drift from the compiled
 * graph in apps/api/agent/graph.py. The previous version hardcoded column
 * offsets row by row; that accumulated off-by-one errors and drew the
 * magenta retry rail beside two flow nodes.
 *
 * Layout family (DESIGN.md section 7, the architecture section contract):
 *   - the nine flow nodes form one vertical chain down the centre,
 *   - broaden_search, the one node the retry loop passes through, sits in a
 *     side box to the left of inspect, where the loop returns,
 *   - the retry path is magenta and traces sufficiency_gate ->
 *     broaden_search -> inspect, entering the side box,
 *   - flow connectors and the pass route read as grey decoration,
 *   - readable characters use ink-9 or ink-7; the box-drawing rules are
 *     ink-5 decoration (DESIGN.md section 7, the diagram legend bullet).
 *
 * No client code: static markup built at render time on the server.
 */

/* --- drawing constants -------------------------------------------------- */

const IW = 19; // box interior width; fits the longest node name
const BOX_W = IW + 2; // box width including both borders
const RAIL_X = 0; // the retry rail runs down column 0
const SIDE_X = 2; // the side box (broaden_search) left border
const MAIN_X = 27; // the main chain left border
const W = MAIN_X + BOX_W; // rows are padded to this width

/**
 * The main-chain column. The side box occupies columns 0..22, the connector
 * between the two boxes occupies 23..25, and the main box starts at 27 so a
 * one-column gutter keeps the connector clear of both borders.
 */

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

/* --- cell grid ---------------------------------------------------------- */

/*
 * Rows are built as a fixed-width character grid, not as concatenated
 * strings. Boxes, connectors, rails and labels all write into the same grid
 * by coordinate, so two segments can never overlap and a connector always
 * lands exactly where its edge says it must. This is the invariant the
 * string-concatenation version could not keep.
 */

type Cell = { ch: string; role: Role };

const blankRow = (): Cell[] =>
  Array.from({ length: W }, () => ({ ch: " ", role: "rule" as Role }));

class Grid {
  rows: Cell[][] = [];

  add(): Cell[] {
    const row = blankRow();
    this.rows.push(row);
    return row;
  }

  put(row: Cell[], x: number, ch: string, role: Role): void {
    if (x < 0 || x >= W) return;
    row[x] = { ch, role };
  }

  text(row: Cell[], x: number, text: string, role: Role): void {
    for (let i = 0; i < text.length; i++) this.put(row, x + i, text[i], role);
  }

  render(): Seg[][] {
    return this.rows.map((row) => {
      // Collapse each row into runs of identical role, so the component
      // emits one span per run instead of one span per character.
      const segs: Seg[] = [];
      let cur: Seg | null = null;
      for (const cell of row) {
        if (cur && cur.role === cell.role) cur.text += cell.ch;
        else {
          cur = { text: cell.ch, role: cell.role };
          segs.push(cur);
        }
      }
      return segs;
    });
  }
}

/* --- the derived drawing ------------------------------------------------ */

function buildDrawing(): { segs: Seg[][] } {
  const { workflow, layerOf } = architecture;
  const { edges, branchLabel } = workflow;

  const flow = edges.filter((e) => e.kind === "flow");
  const retries = edges.filter((e) => e.kind === "retry");

  /* Derive the flow chain: start at the node no flow edge points at, then
   * follow flow edges. Nothing here assumes a node order. */
  const chain: string[] = [];
  {
    const seen = new Set<string>();
    const start = flow.find((e) => !flow.some((o) => o.to === e.from))!.from;
    let cursor: string | undefined = start;
    while (cursor && !seen.has(cursor)) {
      seen.add(cursor);
      chain.push(cursor);
      cursor = flow.find((e) => e.from === cursor)?.to;
    }
  }

  /* The retry loop is gate -> broaden_search -> inspect. The two endpoints
   * live in the chain (the gate and inspect); the bridge node does not. */
  const retryFrom = retries[0]!.from; // sufficiency_gate
  const bridge = retries[0]!.to; // broaden_search
  const retryInto = retries[1]!.to; // inspect
  const gateIdx = chain.indexOf(retryFrom);
  const intoIdx = chain.indexOf(retryInto);

  const grid = new Grid();
  const notchCol = MAIN_X + (IW >> 1) + 1;

  /* Track the row index of each chain node as it is drawn. The loop row
   * emits four rows with no trailing arrow, so the stride is not uniform and
   * any computed index after it would be wrong: the rail must aim at the
   * box's real top row. */
  const boxTopRow: number[] = [];
  let bridgeTopRow = 0;

  /* Draw one flow node. `gate` marks the node that owns the retry edge: its
   * top border takes the magenta arrival notch. `outgoing` controls whether
   * the bottom border carries the connector notch. `at` records the chain
   * position so the magenta rail can aim at this box's real top row. */
  const flowBox = (
    name: string,
    gate: boolean,
    outgoing: boolean,
    at: number
  ): void => {
    boxTopRow[at] = grid.rows.length;
    const top = grid.add();
    grid.text(top, MAIN_X, "┌" + "─".repeat(IW) + "┐", "rule");
    if (gate) grid.put(top, MAIN_X - 1, "┤", "retry");

    const nameRow = grid.add();
    grid.put(nameRow, MAIN_X, "│", "rule");
    grid.text(nameRow, MAIN_X + 1, name.padEnd(IW), "name");
    grid.put(nameRow, MAIN_X + BOX_W - 1, "│", "rule");

    const layerRow = grid.add();
    grid.put(layerRow, MAIN_X, "│", "rule");
    grid.text(layerRow, MAIN_X + 1, (layerOf[name] ?? "").padEnd(IW), "layer");
    grid.put(layerRow, MAIN_X + BOX_W - 1, "│", "rule");

    boxBottom(outgoing);
  };

  const flowArrow = (): void => {
    const row = grid.add();
    grid.put(row, notchCol, "▼", "rule");
  };

  /* Every flow box bottom carries a centre notch, so the arrow that leaves
   * it has something to attach to. A box with no outgoing edge draws the
   * plain rule instead. */
  const boxBottom = (notch: boolean): void => {
    const row = grid.add();
    let x = MAIN_X;
    grid.put(row, x++, "└", "rule");
    const half = (IW - 1) >> 1;
    for (let i = 0; i < half; i++) grid.put(row, x++, "─", "rule");
    grid.put(row, x++, notch ? "┬" : "─", "rule");
    for (let i = 0; i < IW - 1 - half; i++) grid.put(row, x++, "─", "rule");
    grid.put(row, x++, "┘", "rule");
  };

  /* The loop row: the bridge node sits in the side box, the return target
   * beside it, and the magenta return enters the bridge box from the left. */
  const loopRow = (): void => {
    bridgeTopRow = grid.rows.length;
    const top = grid.add();
    grid.text(top, SIDE_X, "┌" + "─".repeat(IW) + "┐", "rule");
    grid.put(top, SIDE_X - 1, "┤", "retry");

    const nameRow = grid.add();
    grid.put(nameRow, SIDE_X - 1, "─", "retry");
    grid.put(nameRow, SIDE_X, "│", "rule");
    grid.text(nameRow, SIDE_X + 1, bridge.padEnd(IW), "name");
    grid.put(nameRow, SIDE_X + BOX_W - 1, "│", "rule");

    const layerRow = grid.add();
    grid.put(layerRow, SIDE_X, "│", "rule");
    grid.text(layerRow, SIDE_X + 1, (layerOf[bridge] ?? "").padEnd(IW), "layer");
    grid.put(layerRow, SIDE_X + BOX_W - 1, "│", "rule");

    const bottom = grid.add();
    grid.text(bottom, SIDE_X, "└" + "─".repeat(IW) + "┘", "rule");
  };

  /* Walk the chain up to the node the retry returns into. */
  for (let i = 0; i < intoIdx; i++) {
    flowBox(chain[i]!, false, true, i);
    flowArrow();
  }
  loopRow();
  /* Walk from the return target to the gate inclusive. */
  for (let i = intoIdx; i < chain.length; i++) {
    const last = i === chain.length - 1;
    flowBox(chain[i]!, chain[i] === retryFrom, !last, i);
    if (!last) flowArrow();
  }

  /* The magenta return, drawn last so it reads over the flow arrows it
   * crosses. It runs from the gate's arrival notch left, up the gutter, and
   * into the bridge box: gate -> broaden_search -> inspect. */
  const gateRow = boxTopRow[gateIdx]!;
  const bridgeRow = bridgeTopRow;

  /* The gate's arrival corner, on the row of its top border. The run reaches
   * the ┤ that flowBox placed one column left of the box border, so the line
   * ends flush against the gate box. */
  grid.rows[gateRow]![RAIL_X] = { ch: "└", role: "retry" };
  for (let x = RAIL_X + 1; x < MAIN_X - 1; x++) {
    grid.rows[gateRow]![x] = { ch: "─", role: "retry" };
  }
  /* The rail ends at the bridge box's top-left entry notch, the ┤ the loop
   * row draws one column left of that box's border, so the return visibly
   * enters the box rather than stopping beside it. */
  const bridgeEntryRow = bridgeRow;
  for (let r = bridgeEntryRow; r < gateRow; r++) {
    grid.rows[r]![RAIL_X] = { ch: "│", role: "retry" };
  }
  /* On the entry row itself, the gutter holds the corner that turns right
   * into the box. The ┤ the loop row placed at column 1 stays, so the two
   * read as one junction. */
  grid.rows[bridgeEntryRow]![RAIL_X] = { ch: "└", role: "retry" };

  /* The RETRY label rides the rail, one row below the gate corner, where the
   * upright is clear of every box. */
  grid.text(grid.rows[gateRow - 1]!, RAIL_X + 2, branchLabel, "retry");

  return { segs: grid.render() };
}

/* --- component ---------------------------------------------------------- */

export function ArchitectureTerminalGraph(): React.JSX.Element {
  const { segs } = buildDrawing();

  return (
    <div className="overflow-x-auto">
      <pre
        data-testid="architecture-terminal-graph"
        data-variant="terminal-graph"
        aria-hidden="true"
        className="m-0 font-term text-[clamp(7px,1.9vw,13px)] leading-4"
      >
        {segs.map((row, i) => (
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
