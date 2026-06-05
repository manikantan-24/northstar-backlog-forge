"""Per-user rate limiting and cost ceiling.

Rolling-window checks keyed by user_id (or "anonymous" for single-user
deployments). Reads from the run-history JSON files that the app writes
after every synthesis so no separate datastore is needed.

Limits (configurable via environment variables):
    RATE_LIMIT_RUNS_PER_HOUR   default: 10
    RATE_LIMIT_COST_PER_DAY    default: 5.00  (USD)

Both limits are soft-enforced: the check runs before the pipeline starts
and raises RateLimitError with a clear message if either is exceeded.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


class RateLimitError(Exception):
    """Raised when a user exceeds their rate or cost limit."""


_MAX_RUNS_PER_HOUR = int(os.environ.get("RATE_LIMIT_RUNS_PER_HOUR", "10"))
_MAX_COST_PER_DAY = float(os.environ.get("RATE_LIMIT_COST_PER_DAY", "5.00"))

# Root of the project — resolved relative to this file.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RUNS_DIR = _PROJECT_ROOT / "logs" / "runs"


def _user_runs_dir(user_id: str) -> Path:
    """Per-user run history directory."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (user_id or "anonymous"))
    return _RUNS_DIR / safe


def _load_runs_for_user(user_id: str) -> list[dict]:
    """Load all persisted run records for a given user. Newest-first."""
    runs_dir = _user_runs_dir(user_id)
    if not runs_dir.exists():
        # Also check the top-level runs dir (legacy / single-user runs).
        runs_dir = _RUNS_DIR
    if not runs_dir.exists():
        return []
    entries: list[dict] = []
    for p in runs_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # Filter to this user's runs if user_id is stored.
            if user_id and user_id != "anonymous":
                if data.get("user_id") and data["user_id"] != user_id:
                    continue
            entries.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries


def check_rate_limit(user_id: str = "anonymous") -> None:
    """Raise RateLimitError if the user exceeds hourly run or daily cost limits.

    Safe to call from the Streamlit main thread — pure file I/O, no network.
    """
    runs = _load_runs_for_user(user_id)
    now = datetime.now(timezone.utc)

    # ---- Hourly run cap ----
    one_hour_ago = now - timedelta(hours=1)
    recent_runs = []
    for r in runs:
        stamp = r.get("timestamp", "")
        try:
            dt = datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if dt >= one_hour_ago:
            recent_runs.append(r)

    if len(recent_runs) >= _MAX_RUNS_PER_HOUR:
        oldest = min(recent_runs, key=lambda r: r.get("timestamp", ""))
        stamp = oldest.get("timestamp", "")
        try:
            dt = datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            reset_in = int((dt + timedelta(hours=1) - now).total_seconds() / 60)
            reset_msg = f" Limit resets in ~{reset_in} minute(s)."
        except (ValueError, TypeError):
            reset_msg = ""
        raise RateLimitError(
            f"Rate limit reached: max {_MAX_RUNS_PER_HOUR} runs per hour "
            f"({len(recent_runs)} in the last 60 minutes).{reset_msg}"
        )

    # ---- Daily cost cap ----
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_cost = 0.0
    for r in runs:
        stamp = r.get("timestamp", "")
        try:
            dt = datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if dt >= midnight:
            try:
                daily_cost += float(r.get("cost_usd") or 0)
            except (TypeError, ValueError):
                pass

    if daily_cost >= _MAX_COST_PER_DAY:
        raise RateLimitError(
            f"Daily cost ceiling reached: ${daily_cost:.4f} spent today "
            f"(limit ${_MAX_COST_PER_DAY:.2f}). Resets at midnight UTC."
        )


def get_usage_summary(user_id: str = "anonymous") -> dict:
    """Return current usage stats for display in the UI sidebar.

    Returns:
        {
            runs_last_hour: int,
            max_runs_per_hour: int,
            cost_today_usd: float,
            max_cost_per_day_usd: float,
        }
    """
    runs = _load_runs_for_user(user_id)
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    runs_last_hour = 0
    cost_today = 0.0

    for r in runs:
        stamp = r.get("timestamp", "")
        try:
            dt = datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if dt >= one_hour_ago:
            runs_last_hour += 1
        if dt >= midnight:
            try:
                cost_today += float(r.get("cost_usd") or 0)
            except (TypeError, ValueError):
                pass

    return {
        "runs_last_hour": runs_last_hour,
        "max_runs_per_hour": _MAX_RUNS_PER_HOUR,
        "cost_today_usd": cost_today,
        "max_cost_per_day_usd": _MAX_COST_PER_DAY,
    }
