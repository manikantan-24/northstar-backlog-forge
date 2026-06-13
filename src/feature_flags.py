"""Admin-configurable feature flags per role.

Loaded from config/feature_flags.yaml at startup. Admins edit the file
(or use the in-app Admin Settings panel) to change what contributors can
do — no code changes needed.

Admins always have full access to every feature; only contributor and
viewer flags are meaningful to configure.

Usage:
    from feature_flags import FeatureFlags
    ff = FeatureFlags.load()
    ff.allowed_presets("contributor")   # → ["free", "balanced"]
    ff.is_enabled("contributor", "compare_mode")  # → True / False
    ff.stage_lock("contributor", "story_writer")  # → "claude-sonnet-4-5" or None
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
FLAGS_PATH = _PROJECT_ROOT / "config" / "feature_flags.yaml"

# Admin capabilities are always fully open — not stored in YAML.
_ADMIN_FLAGS: dict[str, Any] = {
    "allowed_presets": ["local", "free", "balanced", "premium"],
    "max_runs_per_hour": 50,
    "max_cost_per_day_usd": 25.00,
    "compare_mode": True,
    "vision_input": True,
    "dry_run_allowed": True,
    "jira_write_back": True,
    "live_jira_read": True,
    "pii_override": True,
    "stage_model_locks": {
        "parser": None,
        "constraint": None,
        "story_writer": None,
        "epic_decomposer": None,
        "gap_detector": None,
    },
}

_DEFAULT_CONTRIBUTOR: dict[str, Any] = {
    "allowed_presets": ["free", "balanced"],
    "max_runs_per_hour": 10,
    "max_cost_per_day_usd": 5.00,
    "compare_mode": True,
    "vision_input": True,
    "dry_run_allowed": True,
    "jira_write_back": False,
    "live_jira_read": False,
    "pii_override": False,
    "stage_model_locks": {
        "parser": None,
        "constraint": None,
        "story_writer": None,
        "epic_decomposer": None,
        "gap_detector": None,
    },
}

_DEFAULT_VIEWER: dict[str, Any] = {
    "allowed_presets": [],
    "max_runs_per_hour": 0,
    "max_cost_per_day_usd": 0.00,
    "compare_mode": False,
    "vision_input": False,
    "dry_run_allowed": False,
    "jira_write_back": False,
    "live_jira_read": False,
    "pii_override": False,
    "stage_model_locks": {},
}

_DEFAULTS: dict[str, dict] = {
    "admin":       _ADMIN_FLAGS,
    "contributor": _DEFAULT_CONTRIBUTOR,
    "viewer":      _DEFAULT_VIEWER,
}

_STAGE_KEYS = ("parser", "constraint", "story_writer", "epic_decomposer", "gap_detector")


class FeatureFlags:
    """Loaded feature-flag state. Immutable after construction."""

    def __init__(self, data: dict[str, dict]) -> None:
        self._data = data

    # ── factory ────────────────────────────────────────────────────────────

    @classmethod
    def load(cls) -> "FeatureFlags":
        """Load from config/feature_flags.yaml, falling back to defaults."""
        raw: dict = {}
        if FLAGS_PATH.exists():
            try:
                import yaml
                with open(FLAGS_PATH, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
            except Exception:  # noqa: BLE001
                raw = {}

        data: dict[str, dict] = {}
        for role, defaults in _DEFAULTS.items():
            merged = copy.deepcopy(defaults)
            overrides = raw.get(role) or {}
            for key, val in overrides.items():
                if key == "stage_model_locks" and isinstance(val, dict):
                    merged["stage_model_locks"] = {
                        **merged.get("stage_model_locks", {}),
                        **{k: (v if v else None) for k, v in val.items()},
                    }
                else:
                    merged[key] = val
            data[role] = merged

        return cls(data)

    @classmethod
    def save(cls, flags_dict: dict) -> None:
        """Persist a flags dict to feature_flags.yaml.

        `flags_dict` should have the same shape as `to_dict()` output,
        but only the contributor and viewer keys are written — admin flags
        are never persisted (they're always fully open).
        """
        import yaml
        to_write = {
            role: flags_dict[role]
            for role in ("contributor", "viewer")
            if role in flags_dict
        }
        FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FLAGS_PATH, "w", encoding="utf-8") as f:
            yaml.dump(to_write, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)

    # ── accessors ──────────────────────────────────────────────────────────

    def _role(self, role: str) -> dict:
        """Return flags for `role`, falling back to viewer defaults."""
        return self._data.get(role, _DEFAULT_VIEWER)

    def allowed_presets(self, role: str) -> list[str]:
        """List of preset keys the role may select (e.g. ['free', 'balanced'])."""
        return list(self._role(role).get("allowed_presets") or [])

    def is_enabled(self, role: str, feature: str) -> bool:
        """Return True if `feature` is enabled for `role`.

        Feature names: compare_mode, vision_input, dry_run_allowed,
                       jira_write_back, live_jira_read, pii_override
        """
        return bool(self._role(role).get(feature, False))

    def stage_lock(self, role: str, stage: str) -> str | None:
        """Model ID that `stage` is locked to for `role`, or None if free."""
        locks = self._role(role).get("stage_model_locks") or {}
        v = locks.get(stage)
        return v if v else None

    def max_runs_per_hour(self, role: str) -> int:
        return int(self._role(role).get("max_runs_per_hour", 10))

    def max_cost_per_day(self, role: str) -> float:
        return float(self._role(role).get("max_cost_per_day_usd", 5.0))

    def to_dict(self) -> dict:
        """Return a deep copy of the internal data (safe to mutate)."""
        return copy.deepcopy(self._data)

    def apply_stage_locks(self, role: str, models: dict[str, str]) -> dict[str, str]:
        """Return a copy of `models` with any role-specific stage locks applied.

        If a stage is locked to a specific model in feature_flags.yaml,
        that lock overrides whatever the contributor selected in the UI.
        The lock is transparent — the UI still shows the contributor's
        selection, but the orchestrator call uses the locked model.
        """
        result = dict(models)
        for stage in _STAGE_KEYS:
            lock = self.stage_lock(role, stage)
            if lock:
                result[stage] = lock
        return result
