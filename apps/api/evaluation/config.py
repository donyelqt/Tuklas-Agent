"""The eval run config: the shape, the validation, and the config id.

api.md §4 declares the ``EvalConfig``; evaluation.md §1 and §6 declare what
a run must PIN. This module holds the shape in one place, so the runner,
the config checker (``scripts/check_eval_config.py``) and the report all
read the same field names.

The config id is derived from the config's own content:

    config_id = "cfg_" + sha256(canonical_json(config - {config_id}))[:16]

where the canonical JSON has sorted keys and no separator spaces. The id
is EXCLUDED from its own derivation — including it would be circular, and
an id that could not be verified would be an id nobody could rely on.

**Why the id matters.** evaluation.md §6: "Same config → same retrieval
metrics exactly; generation within ±5pp." The id is what makes "the same
config" a checkable statement rather than a claim: two runs with the same
id ran the same thing, and two runs with different ids did not.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

#: The backends api.md §4 allows.
ALLOWED_BACKENDS: frozenset[str] = frozenset({"ollama", "llamacpp", "vllm"})

#: The fuzzy scorer's threshold range (api.md §4: 80..95).
FUZZY_THRESHOLD_RANGE: tuple[int, int] = (80, 95)

#: The retrieval weight components (retrieval.md §3) and their legal range.
WEIGHT_COMPONENTS: tuple[str, ...] = ("lex", "ident", "title", "ref", "hier")
WEIGHT_RANGE: tuple[float, float] = (0.0, 2.0)

#: The margin keys (api.md §4).
MARGIN_KEYS: tuple[str, ...] = ("recall5", "faithfulness", "max_category_gap")

#: The generation fields that must be identical across arms (evaluation.md
#: §1: "Arms differ only in retrieval").
PARITY_FIELDS: tuple[str, ...] = ("model", "backend", "temperature", "seed")

#: The number of hex characters in the config id.
CONFIG_ID_LENGTH = 16

#: The top-level keys a run config may carry. The set is CLOSED: an
#: unrecognised key that changed nothing about the run would still change
#: the id, and a key that DID change the run would change the id
#: silently, which is the opposite of what a config id is for.
TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"config_id", "dataset_snapshot", "arms", "k", "config"}
)

#: The keys the EvalConfig carries (api.md §4). The canonicalisation
#: covers the whole file, so this set is the closed vocabulary.
CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "code_version",
        "weights",
        "normalization",
        "fuzzy_threshold",
        "bonus_flags",
        "stoplist_version",
        "model",
        "backend",
        "temperature",
        "seed",
        "judge_prompt_version",
        "margins",
    }
)


class ConfigError(Exception):
    """The run config is invalid.

    The message names the offending field. A config that fails the parity
    check is an INVALID RUN (api.md §4: "500 EVAL_FAILED (runner crash)",
    and evaluation.md §1: "Different models across arms = invalid run"), so
    the failure is loud and names the arm and the field.
    """


def canonical_json(value: Any) -> str:
    """The canonical JSON form used for hashing: sorted keys, no spaces."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def config_payload(config: dict[str, Any]) -> dict[str, Any]:
    """The part of a run config the id is derived over.

    The ``config_id`` key is excluded, because including the id in its own
    derivation is circular: declaring an id would change it, and the id
    could never be verified.

    Every OTHER key is included, including any key this module does not
    itself validate. That is deliberate: the id must change when anything
    that could affect the run changes, and it must not change when nothing
    does.
    """
    return {key: value for key, value in config.items() if key != "config_id"}


def derive_config_id(config: dict[str, Any]) -> str:
    """The deterministic id of a run config.

    The same config always yields the same id, and any change to the
    config yields a different one.
    """
    payload = config_payload(config)
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"cfg_{digest[:CONFIG_ID_LENGTH]}"


@dataclass(frozen=True)
class RunConfig:
    """One validated run config."""

    dataset_snapshot: str
    arms: dict[str, dict[str, Any]]
    k: list[int]
    config: dict[str, Any]
    config_id: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def model(self) -> str:
        """The generation model every arm used."""
        return str(self.config["model"])

    @property
    def backend(self) -> str:
        """The generation backend every arm used."""
        return str(self.config["backend"])

    @property
    def temperature(self) -> float:
        """The sampling temperature (0, fixed)."""
        return float(self.config["temperature"])

    @property
    def seed(self) -> int:
        """The pinned seed."""
        return int(self.config["seed"])

    @property
    def judge_prompt_version(self) -> str:
        """The judge prompt's version."""
        return str(self.config["judge_prompt_version"])

    @property
    def code_version(self) -> str:
        """The code version the run was made with."""
        return str(self.config["code_version"])

    @property
    def weights(self) -> dict[str, float]:
        """The retrieval weights snapshot."""
        return {key: float(value) for key, value in self.config["weights"].items()}

    @property
    def fuzzy_threshold(self) -> int:
        """The shared scorer's fuzzy threshold."""
        return int(self.config["fuzzy_threshold"])

    def to_dict(self) -> dict[str, Any]:
        """The config as the JSON file spells it, with the derived id."""
        payload = dict(self.raw) if self.raw else {
            "dataset_snapshot": self.dataset_snapshot,
            "arms": self.arms,
            "k": self.k,
            "config": self.config,
        }
        payload["config_id"] = self.config_id
        return payload


def _is_number(value: Any) -> bool:
    """True for a real number (and not a bool)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _as_float(value: Any) -> float | None:
    """The value as a float, or None when it is not a number.

    The helper exists so the checks can narrow the type without a
    suppression comment. CONSTRAINTS.md bans ``# type: ignore``, and a
    narrowing helper is the honest alternative: it says what it does and
    it does it in one place.
    """
    if not _is_number(value):
        return None
    return float(value)


def _check_weights(config: dict[str, Any], violations: list[str]) -> None:
    """The five retrieval weights, each in [0, 2] (retrieval.md §3)."""
    weights = config.get("weights")
    if not isinstance(weights, dict):
        violations.append("config.weights: must be an object")
        return
    for component in WEIGHT_COMPONENTS:
        value = _as_float(weights.get(component))
        if value is None:
            violations.append(f"config.weights.{component}: missing or not a number")
            continue
        if not WEIGHT_RANGE[0] <= value <= WEIGHT_RANGE[1]:
            violations.append(
                f"config.weights.{component}: {value} is outside the legal range "
                f"{list(WEIGHT_RANGE)}"
            )


def _check_bonus_flags(config: dict[str, Any], violations: list[str]) -> None:
    """The two bonus flags (api.md §4)."""
    flags = config.get("bonus_flags")
    if not isinstance(flags, dict):
        violations.append("config.bonus_flags: must be an object")
        return
    for name in ("phrase", "fuzzy"):
        if not isinstance(flags.get(name), bool):
            violations.append(f"config.bonus_flags.{name}: must be a boolean")


def _check_margins(config: dict[str, Any], violations: list[str]) -> None:
    """The three provisional margins (api.md §4)."""
    margins = config.get("margins")
    if not isinstance(margins, dict):
        violations.append("config.margins: must be an object")
        return
    for name in MARGIN_KEYS:
        if not _is_number(margins.get(name)):
            violations.append(f"config.margins.{name}: must be a number")


def _arm_blocks(
    run: dict[str, Any], config: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """The per-arm generation blocks, in whichever shape the config uses.

    Four shapes are accepted, because api.md §4 writes ``arms:
    ["tuklas","vector"]`` (a list of names) and a config that names its
    arms as a list still has to say what model each arm used:

    * ``arms`` as an OBJECT of per-arm blocks — the shape this module
      writes;
    * ``arm_generation`` as a map of per-arm blocks;
    * ``arms`` as a list of names, with the run-level ``config`` block
      describing the generation every arm shares;
    * ``arms`` as a list of per-arm objects.
    """
    arms = run.get("arms")
    if isinstance(arms, dict):
        blocks: dict[str, dict[str, Any]] = {}
        for name, block in arms.items():
            if isinstance(block, dict):
                blocks[str(name)] = block
        return blocks
    generation = run.get("arm_generation")
    if isinstance(generation, dict):
        blocks = {}
        for name, block in generation.items():
            if isinstance(block, dict):
                blocks[str(name)] = block
        return blocks
    if isinstance(arms, list):
        blocks = {}
        for entry in arms:
            if isinstance(entry, dict):
                name = str(entry.get("arm", entry.get("name", "")))
                if name:
                    blocks[name] = entry
            else:
                blocks[str(entry)] = dict(config)
        return blocks
    return {}


def check_run_config(run: dict[str, Any]) -> tuple[list[str], str]:
    """Every violation in a run config, and the derived config id.

    The returned list is EMPTY when the config is valid. The checks, in
    the order they are reported:

    1. the top-level shape (the closed key set);
    2. the EvalConfig's own shape (the closed key set, the weights, the
       flags, the margins, the ranges);
    3. the model PARITY across arms — the rule evaluation.md §1 states as
       "Arms differ only in retrieval. Different models across arms =
       invalid run";
    4. the run-level pins: the snapshot, the K values (which must include
       the primary K, 5), the judge prompt version, the code version.
    """
    violations: list[str] = []
    if not isinstance(run, dict):
        return ["the run config must be a JSON object"], ""

    unknown = sorted(set(run) - TOP_LEVEL_KEYS)
    if unknown:
        violations.append(f"unknown top-level keys {unknown}")

    config = run.get("config")
    if not isinstance(config, dict):
        violations.append("config: missing or not an object")
        config = {}

    unknown_config = sorted(set(config) - CONFIG_KEYS)
    if unknown_config:
        violations.append(f"unknown config keys {unknown_config}")

    for name in ("code_version", "normalization", "stoplist_version", "judge_prompt_version"):
        value = config.get(name)
        if not isinstance(value, str) or not value.strip():
            violations.append(f"config.{name}: missing or empty (the run must pin it)")

    model = config.get("model")
    if not isinstance(model, str) or not model.strip():
        violations.append("config.model: missing or empty")
    backend = config.get("backend")
    if backend not in ALLOWED_BACKENDS:
        violations.append(f"config.backend: must be one of {sorted(ALLOWED_BACKENDS)}")

    temperature = _as_float(config.get("temperature"))
    if temperature is None:
        violations.append("config.temperature: must be a number")
    elif temperature != 0.0:
        violations.append(
            f"config.temperature: must be 0 (api.md §4 fixes it), got {temperature}"
        )

    seed = config.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        violations.append("config.seed: must be an integer")

    threshold = config.get("fuzzy_threshold")
    if not isinstance(threshold, int) or isinstance(threshold, bool):
        violations.append("config.fuzzy_threshold: must be an integer")
    elif not FUZZY_THRESHOLD_RANGE[0] <= threshold <= FUZZY_THRESHOLD_RANGE[1]:
        violations.append(
            f"config.fuzzy_threshold: must be between {FUZZY_THRESHOLD_RANGE[0]} and "
            f"{FUZZY_THRESHOLD_RANGE[1]}, got {threshold}"
        )

    _check_weights(config, violations)
    _check_bonus_flags(config, violations)
    _check_margins(config, violations)

    snapshot = run.get("dataset_snapshot")
    if not isinstance(snapshot, str) or not snapshot.strip():
        violations.append("dataset_snapshot: missing or empty (the run must pin it)")

    k_values = run.get("k")
    if not isinstance(k_values, list) or not k_values:
        violations.append("k: must be a non-empty list")
    else:
        for value in k_values:
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                violations.append(f"k: {value!r} is not a positive integer")
        if 5 not in k_values:
            violations.append("k: must include 5 (Recall@5 is the primary metric)")

    # --- the parity rule --------------------------------------------------
    blocks = _arm_blocks(run, config)
    if not blocks:
        violations.append("arms: no per-arm generation block could be read")
    for name, block in sorted(blocks.items()):
        for field_name in PARITY_FIELDS:
            if field_name not in block:
                violations.append(
                    f"arms.{name}.{field_name}: missing (every arm must name its run)"
                )
        arm_temperature = _as_float(block.get("temperature"))
        if arm_temperature is not None and arm_temperature != 0.0:
            violations.append(
                f"arms.{name}.temperature: must be 0, got {block['temperature']}"
            )

    if len(blocks) >= 2:
        names = sorted(blocks)
        for field_name in PARITY_FIELDS:
            values = {name: blocks[name].get(field_name) for name in names}
            distinct = {repr(value) for value in values.values()}
            if len(distinct) > 1:
                detail = ", ".join(f"{name}={values[name]!r}" for name in names)
                violations.append(
                    f"model parity: the arms disagree on {field_name} ({detail}); "
                    "the arms must differ only in retrieval (evaluation.md §1)"
                )
        for field_name in PARITY_FIELDS:
            if field_name not in config:
                continue
            for name in names:
                block = blocks.get(name, {})
                if field_name in block and block[field_name] != config[field_name]:
                    violations.append(
                        f"arms.{name}.{field_name}: {block[field_name]!r} "
                        f"disagrees with config.{field_name} ({config[field_name]!r}); "
                        "the recorded run-level value must be the one that answered"
                    )

    declared = run.get("config_id")
    config_id = derive_config_id(run)
    if declared is not None and declared != config_id:
        violations.append(
            f"config_id: declared {declared!r} but the content derives {config_id!r}"
        )

    return violations, config_id


def load_run_config(payload: dict[str, Any]) -> RunConfig:
    """A validated :class:`RunConfig` from a parsed JSON config.

    Raises :class:`ConfigError` when the config is invalid. The message
    lists every violation, so a caller sees all of them at once rather
    than one per run.
    """
    violations, config_id = check_run_config(payload)
    if violations:
        raise ConfigError(
            "the run config is invalid:\n  - " + "\n  - ".join(violations)
        )
    config = payload["config"]
    return RunConfig(
        dataset_snapshot=str(payload["dataset_snapshot"]),
        arms=_arm_blocks(payload, config),
        k=[int(value) for value in payload["k"]],
        config=config,
        config_id=config_id,
        raw=dict(payload),
    )


__all__ = [
    "ALLOWED_BACKENDS",
    "CONFIG_ID_LENGTH",
    "CONFIG_KEYS",
    "ConfigError",
    "FUZZY_THRESHOLD_RANGE",
    "MARGIN_KEYS",
    "PARITY_FIELDS",
    "RunConfig",
    "TOP_LEVEL_KEYS",
    "WEIGHT_COMPONENTS",
    "WEIGHT_RANGE",
    "canonical_json",
    "check_run_config",
    "config_payload",
    "derive_config_id",
    "load_run_config",
]
