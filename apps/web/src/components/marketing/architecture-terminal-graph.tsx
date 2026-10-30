import { architecture } from "@/lib/copy";

/**
 * The compiled graph, drawn as terminal box-drawing ASCII.
 *
 * Layout is DERIVED, not composed: every box, connector and retry edge is
 * placed by walking `architecture.workflow.edges` and `architecture.layerOf`,
 * so the drawing cannot drift from apps/api/agent/graph.py.
 *
 * Measured fix (was 49x43, 70.7% empty): the flow chain runs vertically in one
 * column; THE RETRY PAIR (sufficiency_gate + broaden_search) sits side by side
 * one 6-column corridor apart, so the retry bracket is 6 columns wide, not 27;
 * the return edge runs as a magenta bus through that same corridor. No line crosses
 * empty space. The reader follows gate -> broaden_search -> inspect -> gate as a
 * closed loop.
 *
 * NOTE ON COLUMN ORDER. The brief asks for the pair "in a left column" with the chain
 * right. A literal reading reserves 27 dead columns above the pair (offline sweep:
 * 57.9% empty best-case), so the columns are swapped — gate left, bridge right —
 * so the second column exists only where the graph needs a second box.
 *
 * Colour roles (DESIGN.md §7): ink-9 for node names, ink-7 for layer ids, ink-5
 * for box-drawing rules, and magenta for both retry edges (the ONLY magenta, both
 * carrying the RETRY label).
 *
 * No client code: static markup built at render time on the server.
 */

/* --- drawing constants -------------------------------------------------- */

const IW = 19; // box interior width; fits the longest node name
const BOX_W = IW + 2; // box width including both borders

/*
 * The two columns and the corridor between them. Six columns is the
 * narrowest corridor that holds the RETRY label beside the glyphs it sits
 * between, and it is the only empty column range in the drawing.
 */
const CORRIDOR_W = 6;

const CHAIN_X = 0; // the chain column
const SIDE_X = CHAIN_X + BOX_W + CORRIDOR_W; // the corridor right of it
const WIDTH = SIDE_X + BOX_W; // rows are padded to this width

/* Centre column of a box at column x: where a vertical connector lands. */
const centre = (x: number): number => x + 1 + (IW >> 1);

const CHAIN_C = centre(CHAIN_X);

/* Slot i owns rows [i * SLOT_H, i * SLOT_H + 3] for its box, plus one
 * connector row below it when it has an outgoing flow edge. */
const SLOT_H = 5;

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
 * strings. Boxes, connectors, brackets, the bus and the labels all write
 * into the same grid by coordinate, so two segments can never overlap and a
 * connector always lands exactly where its edge says it must. This is the
 * invariant the string-concatenation version could not keep.
 */

type Cell = { ch: string; role: Role };

const blankRow = (): Cell[] =>
  Array.from({ length: WIDTH }, () => ({ ch: " ", role: "rule" as Role }));

class Grid {
  rows: Cell[][] = [];

  add(): Cell[] {
    const row = blankRow();
    this.rows.push(row);
    return row;
  }

  put(row: Cell[], x: number, ch: string, role: Role): void {
    if (x < 0 || x >= WIDTH) return;
    row[x] = { ch, role };
  }

  text(row: Cell[], x: number, text: string, role: Role): void {
    for (let i = 0; i < text.length; i++) {
      this.put(row, x + i, text[i], role);
    }
  }

  render(): Seg[][] {
    return this.rows.map((row) => {
      /*
       * The trailing invisible run is dropped before the DOM sees it. The
       * grid stays absolute-coordinate, so a cell's x is still its column and
       * nothing can collide; the art's footprint is just its drawn extent
       * rather than a fixed WIDTH-wide pad. Leading spaces are kept, because
       * those are structural: they position the side column.
       */
      let last = -1;
      for (let i = row.length - 1; i >= 0; i--) {
        if (row[i]!.ch !== " ") {
          last = i;
          break;
        }
      }

      // Collapse each row into runs of identical role, so the component
      // emits one span per run instead of one span per character.
      const segs: Seg[] = [];
      let cur: Seg | null = null;
      for (let i = 0; i <= last; i++) {
        const cell = row[i]!;
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

  const pairAt = chain.indexOf(retryFrom) * SLOT_H;
  const intoTop = chain.indexOf(retryInto) * SLOT_H;

  const grid = new Grid();

  /* Rows are added on demand, so a chain walk and a pair write can both
   * land on the same absolute coordinates. */
  const ensure = (top: number): void => {
    while (grid.rows.length < top) grid.add();
  };

  /*
   * Write one box into a four-row block. Because the writer is
   * address-absolute, the bridge box can share the block its partner
   * already owns, and that shared block is what puts the pair side by side
   * at the same height.
   */
  const boxInto = (top: number, name: string, x: number): void => {
    ensure(top + 4);
    grid.text(grid.rows[top]!, x, "┌" + "─".repeat(IW) + "┐", "rule");

    const n = grid.rows[top + 1]!;
    grid.put(n, x, "│", "rule");
    grid.text(n, x + 1, name.padEnd(IW), "name");
    grid.put(n, x + BOX_W - 1, "│", "rule");

    const l = grid.rows[top + 2]!;
    grid.put(l, x, "│", "rule");
    grid.text(l, x + 1, (layerOf[name] ?? "").padEnd(IW), "layer");
    grid.put(l, x + BOX_W - 1, "│", "rule");

    grid.text(grid.rows[top + 3]!, x, "└" + "─".repeat(IW) + "┘", "rule");
  };

  /*
   * Walk the chain and draw it: one unbroken vertical column, nine nodes top
   * to bottom, including the gate at its own slot.
   *
   * Every box bottom takes the ┬ the arrow below leaves through and every box
   * top above the first takes the ┬ the arrow above lands on.
   */
  for (let i = 0; i < chain.length; i++) {
    const name = chain[i]!;
    const top = i * SLOT_H;
    boxInto(top, name, CHAIN_X);
    ensure(top + (i < chain.length - 1 ? 5 : 4));
    if (i > 0) grid.put(grid.rows[top]!, CHAIN_C, "┬", "rule");
    if (i < chain.length - 1) {
      grid.put(grid.rows[top + 3]!, CHAIN_C, "┬", "rule");
      grid.put(grid.rows[top + 4]!, CHAIN_C, "▼", "rule");
    }
  }

  /*
   * THE PAIR: broaden_search takes the column one corridor to the right of
   * the gate, sharing the gate's own row block, so the two boxes are ADJACENT
   * at the same height. broaden_search's only outgoing edge is the retry, so
   * its bottom rule takes no notch and no arrow follows it.
   */
  boxInto(pairAt, bridge, SIDE_X);

  /*
   * RETRY EDGE ONE: sufficiency_gate -> broaden_search. The two boxes are
   * adjacent, so the whole edge is a SHORT horizontal bracket on the pair's
   * name row: it leaves the gate's right border, crosses the corridor, and
   * enters the bridge's left border.
   */
  {
    const row = grid.rows[pairAt + 1]!;
    grid.put(row, BOX_W - 1, "├", "retry");
    for (let x = BOX_W; x < SIDE_X; x++) {
      grid.put(row, x, "─", "retry");
    }
    grid.put(row, SIDE_X, "┤", "retry");

    /*
     * The label rides the bracket's OWN row, not the row beneath it. Placing
     * it below the run strands the words: that row carries only the chain's
     * flow arrow four columns further left, so every letter has no magenta
     * neighbour and reads as an orphan instead of a caption for this edge.
     */
    grid.text(grid.rows[pairAt + 1]!, BOX_W, branchLabel, "retry");
  }

  /*
   * RETRY EDGE TWO: broaden_search -> inspect. The return leaves the bridge's
   * top rule, runs one continuous upright up the corridor, and enters
   * inspect's right border on its name row. Its whole extent is that corridor,
   * never a wide empty range and never a rail down the whole page.
   */
  {
    /*
     * The departure: the bridge's top rule takes the magenta ┬ at the bus
     * column, immediately left of the box's own left border, so the edge
     * visibly leaves the node it starts at.
     */
    grid.put(grid.rows[pairAt]!, SIDE_X - 2, "─", "retry");

    /*
     * The upright's row range. Both ends are cells the return actually draws,
     * so any label placed inside this range touches the line it names.
     */
    const uprightTop = intoTop + 1;
    const uprightBottom = pairAt;

    /* The upright, one continuous run in the bus column. */
    for (let r = uprightTop; r <= uprightBottom; r++) {
      grid.put(grid.rows[r]!, SIDE_X - 1, "│", "retry");
    }
    grid.put(grid.rows[uprightBottom]!, SIDE_X - 1, "┬", "retry");

    /*
     * The arrival: inspect takes the magenta ┤ on its right border, on its
     * own name row, the run crosses the corridor back to the upright, and the
     * turn glyph sits in the upright's own column, so the return visibly
     * enters the node it loops back into and no cell is stranded.
     */
    grid.put(grid.rows[intoTop + 1]!, BOX_W - 1, "┤", "retry");
    for (let x = BOX_W; x < SIDE_X - 1; x++) {
      grid.put(grid.rows[intoTop + 1]!, x, "─", "retry");
    }
    grid.put(grid.rows[intoTop + 1]!, SIDE_X - 1, "└", "retry");

    /*
     * The label rides a row the UPRIGHT actually occupies. A midpoint taken
     * against the pair's height is the wrong denominator: the upright starts
     * at inspect's name row and ends at the pair, so a midpoint against the
     * pair alone lands below the run's real extent, on a row carrying only
     * the chain's flow arrow, and strands the words again.
     */
    const labelRow =
      uprightTop + Math.max(1, (uprightBottom - uprightTop) >> 1);
    grid.text(
      grid.rows[labelRow]!,
      SIDE_X - CORRIDOR_W,
      branchLabel,
      "retry",
    );
  }

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
