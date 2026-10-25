import { architecture } from "@/lib/copy";

/**
 * The compiled graph, drawn as terminal box-drawing ASCII.
 *
 * One clean vertical spine of nine boxes and no drawn retry rail at all.
 * The forward chain reads top to bottom as a single column, and the one
 * cycle in the graph is carried by the box that owns it rather than by
 * geometry: `sufficiency_gate` takes a magenta border, the `RETRY` label
 * inside the box, and one magenta annotation line directly beneath it
 * naming the loop, `> broaden_search > inspect`.
 *
 * Why this differs from the shipped drawing. The shipped version ran a
 * 21-character box for `expand_or_backtrack` beside the whole chain, which
 * forced 27 unused columns and left any back edge crossing them reading as
 * a line through nothing. Here every column the art occupies sits inside a
 * box, so there is no empty range for a rail to cross in the first place,
 * and the cycle is stated in words the reader does not have to infer from
 * spacing.
 *
 * Everything is derived from `architecture.workflow.edges`,
 * `architecture.workflow.steps` and `architecture.layerOf` at render time:
 *   - the spine is the flow chain walked off the edge table, with the retry
 *     bridge inserted where `workflow.steps` says the graph visits it,
 *   - the gate is the node the retry edges leave, never a hardcoded name,
 *   - the annotation names the retry edges' targets in edge order,
 *   - box width is the widest name-plus-layer-id row, not a chosen number.
 *
 * Colour roles (DESIGN.md section 7, the diagram legend bullet):
 *   - readable characters use ink-9 (node name) or ink-7 (layer id),
 *   - box-drawing rules are ink-5 decoration only,
 *   - magenta is the retry path alone: the gate border, the RETRY label
 *     inside it, and the annotation line beneath it. No connector and no
 *     rail is magenta, so no magenta glyph is left orphaned.
 *
 * No client code: static markup built at render time on the server.
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
 * strings. Boxes, connectors and the annotation all write into the same
 * grid by coordinate, so two segments can never overlap. This is the
 * invariant the string-concatenation version could not keep.
 */

type Cell = { ch: string; role: Role };

const blankRow = (width: number): Cell[] =>
  Array.from({ length: width }, () => ({ ch: " ", role: "rule" as Role }));

class Grid {
  rows: Cell[][] = [];

  constructor(private readonly width: number) {}

  add(): Cell[] {
    const row = blankRow(this.width);
    this.rows.push(row);
    return row;
  }

  put(row: Cell[], x: number, ch: string, role: Role): void {
    if (x < 0 || x >= this.width) return;
    row[x] = { ch, role };
  }

  text(row: Cell[], x: number, text: string, role: Role): void {
    for (let i = 0; i < text.length; i++) {
      this.put(row, x + i, text[i], role);
    }
  }

  render(): Seg[][] {
    // Collapse each row into runs of identical role, so the component emits
    // one span per run instead of one span per character.
    return this.rows.map((row) => {
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
  const { edges, steps, branchLabel } = workflow;

  const flow = edges.filter((e) => e.kind === "flow");
  const retries = edges.filter((e) => e.kind === "retry");

  /* The forward chain, walked off the flow edges: start at the node no flow
   * edge points at, then follow. Nothing here assumes a node order, so the
   * forward path cannot drift from apps/api/agent/graph.py. */
  const chain: string[] = [];
  {
    const seen = new Set<string>();
    const start = flow.find((e) => !flow.some((o) => o.to === e.from))?.from;
    let cursor: string | undefined = start;
    while (cursor && !seen.has(cursor)) {
      seen.add(cursor);
      chain.push(cursor);
      cursor = flow.find((e) => e.from === cursor)?.to;
    }
  }

  /* The bridge is the one node the retry loop passes through. It has no
   * flow edge of its own, so an edge-only walk cannot place it, and
   * `workflow.steps` is the one table that says where the graph visits it.
   * It is inserted after every chain node that precedes it in that order. */
  const bridge = retries[0]?.to ?? "";
  const bridgeAt = steps.findIndex((step) => step.name === bridge);
  const before = new Set(
    steps.slice(0, bridgeAt < 0 ? 0 : bridgeAt).map((step) => step.name)
  );
  let insert = 0;
  while (insert < chain.length && before.has(chain[insert]!)) insert++;
  const spine = [...chain.slice(0, insert), bridge, ...chain.slice(insert)];

  /* The gate is the node the retry edges leave. Everything magenta keys off
   * this one derived name. */
  const gate = retries[0]?.from ?? "";

  /* The annotation names the loop targets in edge order: where the gate
   * sends the run, and where that node hands it back. */
  const annotation = `> ${retries.map((e) => e.to).join(" > ")}`;

  /* Width is derived, not chosen. The interior is exactly as wide as the
   * widest name-plus-layer-id row, so no box is wider than its content and
   * no line wraps. The canvas then has to be wide enough for the
   * annotation too, or the one line that names the cycle would clip. */
  let widest = 0;
  for (const name of spine) {
    const layerId = layerOf[name] ?? "";
    widest = Math.max(widest, name.length + 2 + layerId.length);
  }
  const IW = Math.max(widest, 1);
  const BOX_W = IW + 2;
  const W = Math.max(BOX_W, annotation.length);
  const X = Math.floor((W - BOX_W) / 2); // spine left border
  const NOTCH_X = X + 1 + ((IW - 1) >> 1); // centre of the box interior

  const grid = new Grid(W);

  /* One node box. `isGate` marks the node that owns the retry edges: its
   * border, the RETRY label inside it and the annotation beneath it are the
   * only magenta in the drawing. `hasNext` decides whether the bottom rule
   * carries the centre notch the outgoing arrow attaches to. */
  const box = (name: string, isGate: boolean, hasNext: boolean): void => {
    const borderRole: Role = isGate ? "retry" : "rule";
    const layerId = layerOf[name] ?? "";

    const top = grid.add();
    grid.text(top, X, "┌" + "─".repeat(IW) + "┐", borderRole);

    /* The name sits at the left edge of the interior; the layer id is
     * right-aligned to the far edge so the column reads as a status field,
     * with at least two columns of air between them. */
    const body = grid.add();
    grid.put(body, X, "│", borderRole);
    grid.text(body, X + 1, name, "name");
    const layerX = Math.max(
      X + 1 + name.length + 2,
      X + 1 + IW - layerId.length
    );
    grid.text(body, layerX, layerId, "layer");
    grid.put(body, X + BOX_W - 1, "│", borderRole);

    if (isGate) {
      const label = grid.add();
      grid.put(label, X, "│", borderRole);
      grid.text(label, X + 1, branchLabel, "retry");
      grid.put(label, X + BOX_W - 1, "│", borderRole);
    }

    const bottom = grid.add();
    grid.put(bottom, X, "└", borderRole);
    const half = (IW - 1) >> 1;
    let x = X + 1;
    for (let i = 0; i < half; i++) grid.put(bottom, x++, "─", borderRole);
    grid.put(bottom, x++, hasNext ? "┬" : "─", borderRole);
    for (let i = 0; i < IW - 1 - half; i++) {
      grid.put(bottom, x++, "─", borderRole);
    }
    grid.put(bottom, X + BOX_W - 1, "┘", borderRole);
  };

  /* The one magenta annotation line, directly beneath the gate box and
   * inside its silhouette so it cannot read as a stray glyph. */
  const annotationRow = (): void => {
    const row = grid.add();
    const start = Math.max(X + 1, X + ((BOX_W - annotation.length) >> 1));
    grid.text(row, start, annotation, "retry");
  };

  /* The flow connector: one arrowhead on the spine's centre line. */
  const arrow = (): void => {
    const row = grid.add();
    grid.put(row, NOTCH_X, "▼", "rule");
  };

  for (let i = 0; i < spine.length; i++) {
    const name = spine[i]!;
    const isGate = name === gate;
    const hasNext = i < spine.length - 1;
    box(name, isGate, hasNext);
    if (isGate) annotationRow();
    if (hasNext) arrow();
  }

  return { segs: grid.render() };
}

/* --- component ---------------------------------------------------------- */

export function ArchitectureTerminalGraph(): React.JSX.Element {
  const { segs } = buildDrawing();

  return (
    <div data-testid="architecture-terminal-graph" className="overflow-x-auto">
      <pre
        data-variant="terminal-graph-annotated-inline"
        aria-hidden="true"
        className="m-0 w-max font-term text-[clamp(7px,1.9vw,13px)] leading-4"
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
