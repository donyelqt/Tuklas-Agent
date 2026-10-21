#!/usr/bin/env python3
"""Eval run-config gate: the model-parity check and the pinned-run invariants.

Run from the repo root:

    python scripts/check_eval_config.py <run-config.json>

Exit 0 + "check_eval_config: OK (<config_id>)" when the config is valid;
exit 1 + one line per violation (`<field>: <problem>`) otherwise.

Why this script exists (CONSTRAINTS.md, "Eval model parity"):

    Same model + backend + temperature(0) + seed across arms.

evaluation.md sec 1 states the same rule and its consequence: "Arms differ
ONLY in retrieval. Different models across arms = invalid run." A run whose
two arms were answered by different models measures the models, not the
retrieval, so the comparison cannot be published.

What it checks:

1. MODEL PARITY. Every arm that names a generation backend must name the
   same model, the same backend, the same temperature and the same seed as
   every other arm. A run config that gives the arms a single shared
   generation block satisfies the rule by construction; a run config that
   gives each arm its own block is checked arm against arm.
2. The pinned run fields. evaluation.md sec 6 says a run must pin the
   dataset snapshot id, the code version, the retrieval weights, the
   normalization id, the fuzzy threshold, the bonus flags, the stoplist
   version, the model and backend, temperature 0, the seed, the judge prompt
   version, the margins and K. A run that does not pin them cannot be
   reproduced or compared, so a missing one is a failure, not a warning.
3. THE CONFIG ID. The id is derived from the canonical form of the config,
   so the same config always yields the same id, and a config that carries
   an id must carry the one its own content derives. That is what makes
   "re-run with the same config id" a checkable statement.
4. THE PINS THAT MUST DESCRIBE THE CODE. The bonus flags and the stoplist
   version are not free-form: they must equal the state the code actually
   has, so a run cannot RECORD a bonus the retrieval layer does not apply
   or a stoplist version nobody can identify. The script reads that state
   from the code itself (the retrieval layer's applied bonuses and the
   agent's stoplist version) rather than declaring a second copy of it.

The config shape is api.md sec 4's EvalConfig, with the per-arm generation
block as the only addition the parity rule needs. Both of these are
accepted:

    {"dataset_snapshot": "...", "arms": ["tuklas", "vector"], "k": [1,3,5,10],
     "config": {"model": "...", "backend": "ollama", "temperature": 0, "seed": 42, ...}}

    {"dataset_snapshot": "...", "arms": ["tuklas", "vector"], "k": [1,3,5,10],
     "config": {"model": "...", "backend": "ollama", "temperature": 0, "seed": 42, ...},
     "arm_generation": {"tuklas": {...}, "vector": {...}}}
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

#: The repo root, so the gate can read the CODE's own state rather than a
#: second copy of it. The script is run as a file (``python
#: scripts/check_eval_config.py``), which puts ``scripts/`` on the path and
#: NOT the repo root, so the root is added here.
REPO_ROOT = Path(__file__).resolve().parent.parent


def _code_state() -> tuple[str, dict[str, bool]]:
    """The CODE's own state: the stoplist version and the applied bonuses.

    The script reads the retrieval layer and the agent's stoplist rather
    than declaring its own copy of them, because the whole point of the
    check is that the config's pins describe the run that HAPPENED. A
    second declaration here would be a second thing to keep in step with
    the first, and the gate would happily accept a config that pinned a
    bonus the layer does not apply.

    The imports are inside the function because the repo root has to be
    on the path before they can resolve, and a module-level import after
    that statement would be an import-not-at-top-of-file.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from apps.api.agent.stoplist import STOPLIST_VERSION as version
    from apps.api.retrieval.scoring import APPLIED_BONUS_FLAGS as applied

    return version, dict(applied)


#: The stoplist version the CODE has (retrieval.md §3: the list is
#: "versioned in code, not ad hoc"). A config that names a different one
#: records a stoplist that was never applied.
STOPLIST_VERSION, APPLIED_BONUS_FLAGS = _code_state()

#: The backends the run config may name (api.md sec 4: ollama|llamacpp|vllm).
ALLOWED_BACKENDS = {"ollama", "llamacpp", "vllm"}

#: The fuzzy scorer's threshold range (api.md sec 4: 80..95).
FUZZY_THRESHOLD_RANGE = (80, 95)

#: The retrieval weight components (retrieval.md sec 3) and their legal range.
WEIGHT_COMPONENTS = ("lex", "ident", "title", "ref", "hier")
WEIGHT_RANGE = (0.0, 2.0)

#: The margin keys (api.md sec 4).
MARGIN_KEYS = ("recall5", "faithfulness", "max_category_gap")

#: The generation fields that must be identical across arms.
PARITY_FIELDS = ("model", "backend", "temperature", "seed")

#: The K values the metrics are reported at. Recall@5 is the PRIMARY metric
#: (evaluation.md sec 3), so 5 must be among them.
REQUIRED_K = 5

#: The number of characters in the config id.
CONFIG_ID_LENGTH = 16

#: The top-level keys a run config may carry. `config_id` is the derived id
#: itself and is excluded from the canonicalisation; every other key is part
#: of it. The set is closed on purpose: an unrecognised key that changed
#: nothing about the run would still change the id, and a key that DID change
#: the run would change the id silently, which is the opposite of what a
#: config id is for.
TOP_LEVEL_KEYS = frozenset({"config_id", "dataset_snapshot", "arms", "k", "config"})

#: The keys the EvalConfig carries (api.md sec 4). The canonicalisation
#: covers the whole file, so this set is the closed vocabulary of a config.
CONFIG_KEYS = frozenset(
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
    """The config could not be read as a JSON object at all."""


def _canonical(value: Any) -> str:
    """The canonical JSON form used for hashing: sorted keys, no spaces."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def config_payload(config: dict[str, Any]) -> dict[str, Any]:
    """The part of a run config the id is derived over.

    The `config_id` key is excluded, because including the id in its own
    derivation is circular: declaring an id would change it, and the id
    could never be verified.

    Every other key of the file is included, INCLUDING any key this script
    does not itself validate. That is deliberate: the id must change when
    anything that could affect the run changes, and it must not change when
    nothing does. The runner derives its id over the same payload, so the
    two agree by construction rather than by a shared list of field names
    that could drift apart.
    """
    return {key: value for key, value in config.items() if key != "config_id"}


def derive_config_id(config: dict[str, Any]) -> str:
    """The deterministic id of a run config.

    `config_id = "cfg_" + sha256(canonical_json(payload))[:16]`, where the
    canonical JSON has sorted keys and no separator spaces.

    The same config always yields the same id, and any change to the config
    yields a different one.
    """
    payload = config_payload(config)
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return f"cfg_{digest[:CONFIG_ID_LENGTH]}"


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _require_str(
    config: dict[str, Any], field: str, violations: list[str]
) -> str | None:
    value = config.get(field)
    if value is None:
        violations.append(f"config.{field}: missing (the run must pin it)")
        return None
    if not isinstance(value, str) or not value.strip():
        violations.append(f"config.{field}: must be a non-empty string")
        return None
    return value


def _require_int(
    config: dict[str, Any], field: str, violations: list[str]
) -> int | None:
    value = config.get(field)
    if value is None:
        violations.append(f"config.{field}: missing (the run must pin it)")
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        violations.append(f"config.{field}: must be an integer")
        return None
    return value


def _check_weights(config: dict[str, Any], violations: list[str]) -> None:
    weights = config.get("weights")
    if weights is None:
        violations.append("config.weights: missing (the run must pin the weight snapshot)")
        return
    if not isinstance(weights, dict):
        violations.append("config.weights: must be an object (the full weight snapshot)")
        return
    missing = [name for name in WEIGHT_COMPONENTS if name not in weights]
    if missing:
        violations.append(f"config.weights: missing component(s) {', '.join(missing)}")
    for name, value in sorted(weights.items()):
        if not _is_number(value):
            violations.append(f"config.weights.{name}: must be a number")
            continue
        low, high = WEIGHT_RANGE
        if not (low <= float(value) <= high):
            violations.append(
                f"config.weights.{name}: {value} is outside the legal range [{low}, {high}]"
            )


def _check_bonus_flags(config: dict[str, Any], violations: list[str]) -> None:
    """The two bonus flags, checked against what the retrieval layer applies.

    A config that pins a bonus the retrieval layer does not apply records
    what was ASKED for and not what was APPLIED, so the run's own record
    would misdescribe the numbers it holds. Such a config is REFUSED here
    rather than accepted and recorded.
    """
    flags = config.get("bonus_flags")
    if flags is None:
        violations.append("config.bonus_flags: missing (the run must pin the bonus flags)")
        return
    if not isinstance(flags, dict):
        violations.append("config.bonus_flags: must be an object")
        return
    for name in ("phrase", "fuzzy"):
        if name not in flags:
            violations.append(f"config.bonus_flags.{name}: missing")
        elif not isinstance(flags[name], bool):
            violations.append(f"config.bonus_flags.{name}: must be a boolean")
        elif flags[name] and not APPLIED_BONUS_FLAGS.get(name):
            violations.append(
                f"config.bonus_flags.{name}: the run pins the {name} bonus, but the "
                "retrieval layer does not apply it (retrieval.md §3 defines no "
                "magnitude for it), so the run would record a bonus that was never "
                "applied"
            )


def _check_margins(config: dict[str, Any], violations: list[str]) -> None:
    margins = config.get("margins")
    if margins is None:
        violations.append("config.margins: missing (the run must pin the margins)")
        return
    if not isinstance(margins, dict):
        violations.append("config.margins: must be an object")
        return
    for name in MARGIN_KEYS:
        if name not in margins:
            violations.append(f"config.margins.{name}: missing")
        elif not _is_number(margins[name]):
            violations.append(f"config.margins.{name}: must be a number")


def _check_generation_block(
    block: dict[str, Any], label: str, violations: list[str]
) -> dict[str, Any]:
    """The shared per-arm generation fields, checked for presence and shape."""
    values: dict[str, Any] = {}
    model = block.get("model")
    if not isinstance(model, str) or not model.strip():
        violations.append(f"{label}.model: missing or empty (the run must name the model)")
    else:
        values["model"] = model

    backend = block.get("backend")
    if not isinstance(backend, str) or not backend.strip():
        violations.append(f"{label}.backend: missing or empty (the run must name the backend)")
    elif backend not in ALLOWED_BACKENDS:
        violations.append(
            f"{label}.backend: {backend!r} is not one of {sorted(ALLOWED_BACKENDS)}"
        )
    else:
        values["backend"] = backend

    temperature = block.get("temperature")
    if temperature is None:
        violations.append(f"{label}.temperature: missing (the run must pin it)")
    elif not _is_number(temperature):
        violations.append(f"{label}.temperature: must be a number")
    elif float(temperature) != 0.0:
        violations.append(
            f"{label}.temperature: {temperature} is not 0 (evaluation.md sec 1 fixes it at 0)"
        )
    else:
        values["temperature"] = 0

    seed = block.get("seed")
    if seed is None:
        violations.append(f"{label}.seed: missing (the run must pin it)")
    elif not isinstance(seed, int) or isinstance(seed, bool):
        violations.append(f"{label}.seed: must be an integer")
    else:
        values["seed"] = seed

    return values


def _arm_blocks(run: dict[str, Any], config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The per-arm generation blocks, in whichever shape the config uses.

    Four shapes are accepted, in this order:

    1. `run["arm_generation"]` / `config["arm_generation"]`: a mapping of
       arm name to a generation block.
    2. `run["arms"]` / `config["arms"]`: an OBJECT mapping arm name to a
       generation block.
    3. `run["arms"]` / `config["arms"]`: a LIST of arm names, with the
       generation block shared in `config`.
    4. `run["arms"]`: a LIST of objects that each name their arm and carry
       a generation block.

    The first two are the shapes the eval runner writes; the last two are
    the shapes api.md sec 4 describes, and they are accepted so a config
    written straight from the contract is still checked rather than skipped.
    """
    for holder in (run, config):
        blocks = holder.get("arm_generation")
        if isinstance(blocks, dict) and blocks:
            out: dict[str, dict[str, Any]] = {}
            for name, block in blocks.items():
                out[str(name)] = block if isinstance(block, dict) else {}
            return out

    for holder in (run, config):
        arms = holder.get("arms")
        if isinstance(arms, dict) and arms:
            out = {}
            for name, block in arms.items():
                out[str(name)] = block if isinstance(block, dict) else {}
            return out

    for holder in (run, config):
        arms = holder.get("arms")
        if isinstance(arms, list) and arms and all(isinstance(a, dict) for a in arms):
            out = {}
            for arm in arms:
                name = arm.get("name") or arm.get("arm")
                if not isinstance(name, str) or not name:
                    continue
                out[name] = arm
            if out:
                return out
    return {}


def _check_parity(
    run: dict[str, Any], config: dict[str, Any], violations: list[str]
) -> None:
    """The parity rule: same model + backend + temperature(0) + seed across arms."""
    arms = run.get("arms")
    if arms is None:
        arms = config.get("arms")
    if arms is None:
        violations.append("arms: missing (the run must name the arms it ran)")
        return

    arm_names: list[str]
    if isinstance(arms, dict):
        arm_names = [str(name) for name in arms]
        if not arm_names:
            violations.append("arms: must name at least one arm")
            return
    elif isinstance(arms, list):
        if not arms:
            violations.append("arms: must be a non-empty list")
            return
        if not all(isinstance(a, str) for a in arms):
            violations.append("arms: must be a list of arm names")
            return
        if len(set(arms)) != len(arms):
            violations.append("arms: the same arm is listed twice")
        arm_names = list(arms)
    else:
        violations.append("arms: must be a list of arm names or an object of arm blocks")
        return

    blocks = _arm_blocks(run, config)
    if blocks:
        per_arm = {
            name: _check_generation_block(block, f"arms.{name}", violations)
            for name, block in blocks.items()
        }
        missing = [name for name in arm_names if name not in per_arm]
        if missing:
            violations.append(f"arms: no generation block for arm(s) {', '.join(missing)}")
        comparable = {name: values for name, values in per_arm.items() if values}
        if len(comparable) >= 2:
            reference_name = sorted(comparable)[0]
            reference = comparable[reference_name]
            for name in sorted(comparable):
                if name == reference_name:
                    continue
                other = comparable[name]
                for field in PARITY_FIELDS:
                    if field not in other:
                        continue
                    if other[field] != reference[field]:
                        violations.append(
                            f"arms.{name}.{field}: {other[field]!r} differs from "
                            f"{reference_name} ({reference[field]!r}) -- the arms must share "
                            "one model, backend, temperature and seed"
                        )
        # The shared generation block in `config` must agree with the arms.
        # A config that names one model for the run and another for an arm
        # is a config whose recorded run-level model is not the one that
        # answered, and the recorded value is the one a reader checks.
        shared = _check_generation_block(config, "config", violations)
        for name, values in sorted(comparable.items()):
            for field in PARITY_FIELDS:
                if field not in shared or field not in values:
                    continue
                if shared[field] != values[field]:
                    violations.append(
                        f"config.{field}: {shared[field]!r} differs from arms.{name} "
                        f"({values[field]!r}) -- the recorded run-level generation must be "
                        "the one the arms used"
                    )
        return

    # No per-arm block: the run names one shared generation block.
    _check_generation_block(config, "config", violations)


def check_run_config(run: dict[str, Any]) -> tuple[list[str], str]:
    """Every violation in the run config, and the derived config id."""
    violations: list[str] = []

    config = run.get("config")
    if config is None:
        violations.append("config: missing (the run must carry an EvalConfig)")
        return violations, ""
    if not isinstance(config, dict):
        violations.append("config: must be an object")
        return violations, ""

    snapshot = run.get("dataset_snapshot", config.get("dataset_snapshot"))
    if not isinstance(snapshot, str) or not snapshot.strip():
        violations.append("dataset_snapshot: missing (the run must name the dataset snapshot)")

    k = run.get("k", config.get("k"))
    if k is None:
        violations.append("k: missing (the run must pin the K values)")
    elif not isinstance(k, list) or not all(isinstance(v, int) for v in k):
        violations.append("k: must be a list of integers")
    elif REQUIRED_K not in k:
        violations.append(
            f"k: {REQUIRED_K} is missing (Recall@5 is the primary metric, evaluation.md sec 3)"
        )

    _require_str(config, "code_version", violations)
    _require_str(config, "normalization", violations)
    _require_str(config, "stoplist_version", violations)
    _require_str(config, "judge_prompt_version", violations)

    # The stoplist version must be the version the CODE has. A config that
    # names a version the code does not have records a stoplist nobody can
    # identify, and the run's own record would then misdescribe the
    # broaden-retry that produced the ranking (retrieval.md sec 3).
    stoplist_version = config.get("stoplist_version")
    if isinstance(stoplist_version, str) and stoplist_version.strip():
        if stoplist_version != STOPLIST_VERSION:
            violations.append(
                f"config.stoplist_version: {stoplist_version!r} is not the stoplist "
                f"this code has ({STOPLIST_VERSION!r}), so the run would record a "
                "stoplist version that was never applied"
            )

    threshold = config.get("fuzzy_threshold")
    if threshold is None:
        violations.append("config.fuzzy_threshold: missing (the run must pin it)")
    elif not isinstance(threshold, int) or isinstance(threshold, bool):
        violations.append("config.fuzzy_threshold: must be an integer")
    else:
        low, high = FUZZY_THRESHOLD_RANGE
        if not (low <= threshold <= high):
            violations.append(
                f"config.fuzzy_threshold: {threshold} is outside [{low}, {high}]"
            )

    _check_weights(config, violations)
    _check_bonus_flags(config, violations)
    _check_margins(config, violations)
    _check_parity(run, config, violations)

    config_id = derive_config_id(run)
    declared = run.get("config_id")
    if declared is not None:
        if not isinstance(declared, str) or not declared.strip():
            violations.append("config_id: must be a non-empty string")
        elif declared != config_id:
            violations.append(
                f"config_id: {declared!r} does not match the id derived from the config "
                f"content ({config_id!r})"
            )

    # Unknown keys are a failure, not a shrug. A key this script does not
    # recognise is a key it cannot check, and a key it cannot check is a
    # key whose value could change the run without changing anything this
    # script knows about.
    for key in sorted(run):
        if key not in TOP_LEVEL_KEYS:
            violations.append(f"{key}: unknown top-level key in the run config")
    for key in sorted(config):
        if key not in CONFIG_KEYS:
            violations.append(f"config.{key}: unknown key in the EvalConfig")

    return violations, config_id


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python scripts/check_eval_config.py <run-config.json>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"check_eval_config: no such file: {path}", file=sys.stderr)
        return 2
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"check_eval_config: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    try:
        run = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"check_eval_config: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(run, dict):
        print(f"check_eval_config: {path} is not a JSON object", file=sys.stderr)
        return 2

    violations, config_id = check_run_config(run)
    if violations:
        for line in violations:
            print(f"check_eval_config: {line}", file=sys.stderr)
        print(f"check_eval_config: FAIL ({len(violations)} violation(s))")
        return 1
    print(f"check_eval_config: OK ({config_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
