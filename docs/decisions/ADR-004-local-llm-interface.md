# ADR-004: Interchangeable local-LLM interface (Ollama default)

## Status
Accepted

## Date
2026-10-07

## Context
Hardware varies (laptop CPU → workstation GPU); no single local model fits all
builders. Generation must work behind one interface so the model can be swapped
without touching retrieval, agent, or evaluation code — and so latency/token
numbers stay comparable across runs only when the model is recorded.

`apps/api/generation/` exposes `LLMInterface` with two methods —
`generate(prompt, stop, max_tokens, *, temperature = 0, seed)` (single-shot, used
by answers and judges) and `generate_stream(prompt, ..., same sampling params)`
(token stream, used by `POST /v1/ask/stream`; backends without native streaming
emulate by yielding sentence chunks). Backend selected by config
(`ollama | llamacpp | vllm`, Ollama default). Model id + backend + temperature +
seed recorded in every ask trace and eval run; eval requires temperature 0 and
equal seed across arms (`api.md §4`).

## Alternatives Considered

### Hardcode one model (e.g. Ollama llama3.1:8b everywhere)
- Pros: simplest; reproducible by default.
- Cons: excludes builders whose hardware can't run it or who have better local options.
- Rejected: local-first means adapting to the machine.

### Cloud LLM fallback (hosted API when local is slow)
- Pros: better answers on weak hardware.
- Cons: document bytes leave the machine — violates the local-first privacy premise
  ("private law document discovery") and poisons the research setup.
- Rejected: never silently; a cloud backend would need its own ADR + explicit user opt-in.

## Consequences
- Prompts versioned with the interface; judge prompts versioned separately (`evaluation.md`).
- Arm comparisons across different models are invalid — CI check on `evaluation_runs.config`.
- LLM-down degrades to 503 `LLM_UNAVAILABLE`, never to a fake answer.
