import sys, tempfile, sqlite3
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
    return (len(sup) > 0), len(ev["kept"])

# section nodes with short text -> label resolves, but dropped from kept
conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
secs = conn.execute(
  "SELECT id,title,length(text) L FROM nodes WHERE type='section' AND length(text) < 50 LIMIT 200").fetchall()
conn.close()
print(f"short section nodes: {len(secs)}")
rescues = []
for r in secs:
    # build "section N" style queries from the node id/title
    import re
    m = re.search(r"section_(\d+)", r["id"])
    if not m: continue
    n = m.group(1)
    for q in [f"section {n}", f"the section {n}", f"said section {n}", f"article section {n}"]:
        ok0, k0 = gate(q)
        b = broaden(q)
        ok1, k1 = gate(b)
        if (not ok0) and ok1:
            rescues.append((q, b, k0, k1, r["id"]))
            break
print(f"\nRESCUES: {len(rescues)}")
for q,b,k0,k1,nid in rescues[:10]:
    print(f"  orig={q!r:26} FAIL(kept {k0}) -> broad={b!r:18} PASS(kept {k1})  [{nid[:44]}]")
