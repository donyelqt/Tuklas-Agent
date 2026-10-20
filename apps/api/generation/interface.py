"""The local-LLM seam (ADR-004, architecture.md sec 6).

One interface, two methods: ``generate`` (single-shot, used by answers and
judges) and ``generate_stream`` (token stream, used by the SSE surface). Both
take the SAME sampling parameters, so a streamed answer and a single-shot
answer of the same prompt are the same run.

Two binding rules live here:

- **The run is recorded.** Model id, backend, temperature and seed travel with
  every result (ADR-004: "Model id + backend + temperature + seed recorded in
  every ask trace and eval run"). The seed is PINNED, never random: an
  unseeded run cannot be reproduced, and a recorded seed is what makes two
  arms comparable.
- **A down backend is a typed failure, never a fake answer.** An unreachable,
  timing-out or erroring backend raises ``LLM_UNAVAILABLE`` (503, api.md sec 1).
  The interface never returns invented text, and it never silently falls back
  to another backend.

The ollama backend speaks ``POST {base_url}/api/generate``. The backend is
selected by configuration; ``ollama`` is the default and the only backend
implemented in v1 (``llamacpp`` and ``vllm`` are ADR-004 options, not code).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
from apps.api.ingestion import errors

_LOG = logging.getLogger(__name__)

#: ADR-004: the backend is selected by config; ollama is the default.
DEFAULT_BACKEND = "ollama"
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"

#: ADR-004 + api.md sec 4: temperature 0 is fixed, and the seed is pinned so
#: two runs of the same prompt are the same run.
DEFAULT_TEMPERATURE = 0.0
DEFAULT_SEED = 42

#: Read timeout. The FIRST call to a model pays a cold start (tens of
#: seconds); later calls are seconds. The timeout has to clear the cold
#: start, or a healthy backend looks dead.
DEFAULT_TIMEOUT = 120.0

#: Connect timeout. Short on purpose: a closed port must fail FAST with a
#: 503 instead of holding the request open for the full read timeout.
CONNECT_TIMEOUT = 5.0

#: The backends this interface can talk to.
#:
#: ADR-004 names three: ``ollama | llamacpp | vllm``, with Ollama the
#: default. Only Ollama is implemented in this slice: the other two are
#: OpenAI-compatible servers, so they are a base_url away, but a backend
#: that is not implemented must not be ACCEPTED and then fail at the first
#: request. The constructor rejects an unimplemented backend by name.
#:
#: The recorded ``backend`` field is the one that actually ran, so a run
#: recorded against one backend is never confused with a run against another.
SUPPORTED_BACKENDS = ("ollama",)


@dataclass(frozen=True)
class GenerationResult:
    """One recorded generation (ADR-004).

    The dataclass is frozen: a result is a record, and a record that can be
    edited after the fact is not a record. ``prompt_tokens`` and
    ``completion_tokens`` are the BACKEND's own counts (``prompt_eval_count``
    and ``eval_count``); they are ``None`` when the backend reported none,
    and they are never estimated here — an estimate is not a measurement, and
    api.md sec 3 requires the consumer to be able to tell the two apart.
    """

    text: str
    model: str
    backend: str
    temperature: float
    seed: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMInterface:
    """The local-LLM interface (ADR-004).

    Constructed with the backend, the model id and the sampling parameters.
    The parameters are read at call time from the instance, so a recorded
    result and the run that produced it cannot disagree.
    """

    def __init__(
        self,
        backend: str = DEFAULT_BACKEND,
        model: str = DEFAULT_MODEL,
        *,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = DEFAULT_TEMPERATURE,
        seed: int = DEFAULT_SEED,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Bind the backend, the model and the sampling parameters.

        ``temperature`` defaults to 0 and ``seed`` to a pinned constant: an
        unseeded run is not reproducible, and a random seed recorded after
        the fact records nothing.
        """
        if backend not in SUPPORTED_BACKENDS:
            raise ValueError(
                f"unsupported backend {backend!r}; supported: {', '.join(SUPPORTED_BACKENDS)}"
            )
        if not model:
            raise ValueError("model id must not be empty")
        self.backend = backend
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = float(temperature)
        self.seed = int(seed)
        self.timeout = float(timeout)

    # -- internals ---------------------------------------------------------

    def _timeout(self) -> httpx.Timeout:
        """Read timeout with a short connect timeout.

        A closed port must fail in seconds, not in the read timeout: a
        backend that is down is a 503 the caller can act on.
        """
        return httpx.Timeout(self.timeout, connect=CONNECT_TIMEOUT)

    def _url(self) -> str:
        """The backend's generation endpoint."""
        return f"{self.base_url}/api/generate"

    def _options(self, stop: list[str] | None, max_tokens: int | None) -> dict[str, Any]:
        """The sampling options, identical for both call paths."""
        options: dict[str, Any] = {"temperature": self.temperature, "seed": self.seed}
        if stop:
            options["stop"] = list(stop)
        if max_tokens is not None:
            options["num_predict"] = int(max_tokens)
        return options

    def _payload(
        self, prompt: str, stop: list[str] | None, max_tokens: int | None, stream: bool
    ) -> dict[str, Any]:
        """The request body for one generation call."""
        return {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
            "options": self._options(stop, max_tokens),
        }

    def _unavailable(self, reason: str) -> errors.ApiError:
        """The typed backend failure (api.md sec 1, ``LLM_UNAVAILABLE`` 503).

        The reason is a transport-level class name, not an internal message:
        api.md sec 1 says no traceback leaks.
        """
        _LOG.warning("llm backend unavailable: %s", reason)
        return errors.llm_unavailable("the local LLM backend is unavailable", reason=reason)

    def _counts(self, data: dict[str, Any]) -> tuple[int | None, int | None]:
        """The backend's own token counts, as ``(prompt, completion)``."""
        prompt_tokens = data.get("prompt_eval_count")
        completion_tokens = data.get("eval_count")
        return (
            int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
            int(completion_tokens) if isinstance(completion_tokens, int) else None,
        )

    # -- public surface ----------------------------------------------------

    def generate(
        self,
        prompt: str,
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> GenerationResult:
        """One complete answer, with the run recorded.

        Raises ``LLM_UNAVAILABLE`` (503) when the backend cannot be reached
        or fails; it never returns a fabricated answer in place of the text
        the model did not produce.
        """
        payload = self._payload(prompt, stop, max_tokens, stream=False)
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                response = client.post(self._url(), json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise self._unavailable(type(exc).__name__) from exc
        except ValueError as exc:
            # A non-JSON body from a healthy-looking backend is still an
            # unusable backend, not an answer.
            raise self._unavailable("invalid-response") from exc
        if not isinstance(data, dict):
            raise self._unavailable("invalid-response")
        text = data.get("response")
        prompt_tokens, completion_tokens = self._counts(data)
        return GenerationResult(
            text=text if isinstance(text, str) else "",
            model=str(data.get("model") or self.model),
            backend=self.backend,
            temperature=self.temperature,
            seed=self.seed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def generate_stream(
        self,
        prompt: str,
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """The answer as a stream of text chunks.

        The sampling parameters are the ones ``generate`` uses, so a streamed
        answer and a single-shot answer are the same run. The backend's own
        streaming is used when it has it; a chunk is yielded as soon as it
        arrives, and the stream ends when the backend says it is done.
        """
        payload = self._payload(prompt, stop, max_tokens, stream=True)
        try:
            with (
                httpx.Client(timeout=self._timeout()) as client,
                client.stream("POST", self._url(), json=payload) as response,
            ):
                response.raise_for_status()
                for line in response.iter_lines():
                    chunk = self._chunk_text(line)
                    if chunk:
                        yield chunk
        except httpx.HTTPError as exc:
            raise self._unavailable(type(exc).__name__) from exc

    def stream_result(
        self,
        prompt: str,
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> tuple[GenerationResult, list[str]]:
        """One complete answer produced by the STREAMING endpoint.

        The backend's streaming response carries the same final counters as
        the single-shot one (the last streamed object holds them), so the
        recorded result is the same shape either way. The deltas are returned
        in the order the backend emitted them, and the text is their
        concatenation - which is the same text the single-shot call returns
        at the same temperature and seed.
        """
        payload = self._payload(prompt, stop, max_tokens, stream=True)
        chunks: list[str] = []
        counts: dict[str, Any] = {}
        try:
            with (
                httpx.Client(timeout=self._timeout()) as client,
                client.stream("POST", self._url(), json=payload) as response,
            ):
                response.raise_for_status()
                for line in response.iter_lines():
                    data = self._stream_object(line)
                    if data is None:
                        continue
                    text = data.get("response")
                    if isinstance(text, str) and text:
                        chunks.append(text)
                    if data.get("done"):
                        counts = data
        except httpx.HTTPError as exc:
            raise self._unavailable(type(exc).__name__) from exc
        prompt_tokens, completion_tokens = self._counts(counts)
        result = GenerationResult(
            text="".join(chunks),
            model=str(counts.get("model") or self.model),
            backend=self.backend,
            temperature=self.temperature,
            seed=self.seed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        # The deltas as the backend emitted them, in its order. The client
        # streams these, so a token event IS a piece of the model's output
        # rather than an arbitrary slice of the finished string.
        return result, chunks

    def _stream_object(self, line: str) -> dict[str, Any] | None:
        """One streamed JSON object, or None when the line carries none.

        A line that is not JSON is skipped rather than aborting the stream:
        one malformed line is not a reason to lose the answer that already
        arrived.
        """
        stripped = line.strip()
        if not stripped:
            return None
        try:
            data = json.loads(stripped)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _chunk_text(self, line: str) -> str:
        """The text of one streamed line, or "" for a line that carries none.

        The backend emits newline-delimited JSON objects; the last one
        carries the counters and no text. A line that is not JSON is skipped
        rather than aborting the stream, because one malformed line is not a
        reason to lose the answer that already arrived.
        """
        stripped = line.strip()
        if not stripped:
            return ""
        try:
            data = json.loads(stripped)
        except ValueError:
            return ""
        if not isinstance(data, dict):
            return ""
        text = data.get("response")
        return text if isinstance(text, str) else ""


__all__ = [
    "CONNECT_TIMEOUT",
    "DEFAULT_BACKEND",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_SEED",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT",
    "GenerationResult",
    "LLMInterface",
    "SUPPORTED_BACKENDS",
]
