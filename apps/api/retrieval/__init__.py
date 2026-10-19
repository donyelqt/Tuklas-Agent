"""Day-2 retrieval engine — vectorless (retrieval.md), zero dense embeddings.

Modules: ``normalizer`` (label grammar), ``index`` (FTS5 + ident + BM25 build),
``scoring`` (two-pass disjoint-component scoring), ``tools`` (the agent's only
tree access), ``context`` (PromptContext budget assembly).

``__init__`` stays import-free so each module loads only when actually used.
"""
