"""Day-4 generation package — prompt rendering, citations, refusal (architecture.md sec 6).

Modules: ``interface`` (the LLM seam and the recorded result), ``prompts``
(the versioned renderer for the opaque PromptContext), ``citations`` (the
citation list), ``blocklist`` (the single advice oracle), ``service`` (the
refusal path and the answer assembly).

``__init__`` stays import-free so each module loads only when actually used.
"""
