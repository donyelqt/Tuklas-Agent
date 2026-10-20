import sys, tempfile, random, sqlite3
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

def gate(q):
    hits = search_nodes(query=q, document_id=doc.id, top_k=10, db_path=db)
    ev = select_evidence(hits, [q], db_path=db)
    kept = {i["node_id"] for i in ev["kept"]}
    sup = [s for s in ev["gate"]["per_question"].get(q, []) if s in kept]
    return len(sup) > 0, ev

# KEY INSIGHT TEST: a query whose tokens match a node whose TEXT is short
# (<50 chars) -> dropped from kept -> 0 supporters -> gate FAILS.
# The broadened query must then hit a DIFFERENT node with long text.
conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, title, text FROM nodes WHERE length(text) BETWEEN 1 AND 49 AND page_start IS NOT NULL LIMIT 300").fetchall()
conn.close()
print(f"short nodes: {len(rows)}")
random.seed(7)
tried = 0; rescues = []
for r in random.sample(rows, min(80, len(rows))):
    words = [w for w in str(r["title"] or "").split() if w.isalpha() and len(w) > 4]
    if not words: continue
    q = "the " + " ".join(words[:2])
    tried += 1
    ok0, ev0 = gate(q)
    b = broaden(q)
    ok1, ev1 = gate(b)
    if (not ok0) and ok1:
        rescues.append((q, b, len(ev0["kept"]), len(ev1["kept"]), r["id"]))
print(f"\ntried={tried} RESCUES={len(rescues)}")
for q,b,k0,k1,nid in rescues[:8]:
    print(f"  orig={q!r:34} FAIL(kept {k0}) -> broad={b!r:26} PASS(kept {k1})  [{nid[:38]}]")
