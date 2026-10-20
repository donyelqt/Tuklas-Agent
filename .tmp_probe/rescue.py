import sys, tempfile, sqlite3, itertools, random
sys.path.insert(0, ".")
from pathlib import Path
from apps.api.ingestion.service import ingest_pdf
from apps.api.retrieval.index import build_indexes
import apps.api.retrieval.scoring as _scoring
from apps.api.retrieval.api import search_nodes, select_evidence
from apps.api.agent.stoplist import broaden

tmp = Path(tempfile.mkdtemp()); db = str(tmp/"cc.db")
doc = ingest_pdf("data/documents/civil-code-ra386-icnl.pdf", slug="civil-code-public", db_path=db)
build_indexes(db, doc.id, index_root=str(tmp/"idx"))
_scoring.INDEX_ROOT = tmp/"idx"

def gate(hits, sq):
    ev = select_evidence(hits, [sq], db_path=db)
    sup = ev["gate"]["per_question"].get(sq, [])
    kept = {i["node_id"] for i in ev["kept"]}
    return (len([s for s in sup if s in kept]) > 0), ev

# Hypothesis: min-max normalization means ANY lexical match gives the top node
# lex=1.0 -> supporter. So the gate fails only when the ONLY matching nodes
# are dropped from kept (<50 chars or no page span).
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
short = [r["id"] for r in conn.execute(
    "SELECT id FROM nodes WHERE length(text) < 50 AND page_start IS NOT NULL LIMIT 400")]
conn.close()
print(f"short nodes (text<50, has page): {len(short)}")

random.seed(42)
rescues = []
for node_id in random.sample(short, min(60, len(short))):
    # build a query from the node's own title words -> lex matches this node
    row_hits = search_nodes(query=node_id.replace("_", " "), document_id=doc.id, top_k=10, db_path=db)
    # find the title
    for q in [node_id.split("_")[-1], " ".join(node_id.split("_")[-2:])]:
        hits = search_nodes(query=q, document_id=doc.id, top_k=10, db_path=db)
        if not hits: continue
        ok0, ev0 = gate(hits, q)
        b = broaden(q)
        hb = search_nodes(query=b, document_id=doc.id, top_k=10, db_path=db)
        ok1, ev1 = gate(hb, b)
        if (not ok0) and ok1:
            rescues.append((q, b, len(ev0["kept"]), len(ev1["kept"])))
            break
print(f"\nRESCUES found: {len(rescues)}")
for q,b,k0,k1 in rescues[:10]:
    print(f"  orig={q!r:28} fail | broad={b!r:24} PASS | kept {k0}->{k1}")
