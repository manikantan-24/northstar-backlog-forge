"""Security and operational alerting.

Provides ``post_security_alert`` — called after any ``SecurityFinding`` with
severity "error" is produced by the pipeline.  Supports three destinations,
selected by environment variable:

  Slack   — ``SECURITY_WEBHOOK_URL`` pointing at an Incoming Webhook URL.
  Teams   — same var; auto-detected by the URL path containing "webhook.office.com".
  Generic — any URL; receives the canonical JSON payload described below.
  PagerDuty — set ``PAGERDUTY_ROUTING_KEY``; uses the Events API v2.

All destinations are fire-and-forget (2-second timeout, logged on failure).
If no destination is configured the function logs at WARNING and returns.

Canonical payload (Slack / generic POST)
-----------------------------------------
{
  "run_id":     "<uuid>",
  "user":       "<email or anonymous>",
  "findings":   [{"code": "...", "severity": "...", "message": "...", "story_id": null}],
  "timestamp":  "2026-06-10T12:34:56Z"
}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from logger_setup import get_logger

logger = get_logger(__name__)

_WEBHOOK_URL       = os.environ.get("SECURITY_WEBHOOK_URL", "").strip()
_SLACK_NOTIFY_URL  = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
_PAGERDUTY_KEY     = os.environ.get("PAGERDUTY_ROUTING_KEY", "").strip()
_MIN_SEVERITY      = os.environ.get("SECURITY_ALERT_MIN_SEVERITY", "error").strip().lower()
_ALERT_TIMEOUT     = 2.0   # seconds — fire-and-forget; never blocks the pipeline

_SEVERITY_RANK = {"info": 0, "warn": 1, "error": 2}


def _should_alert(severity: str) -> bool:
    return _SEVERITY_RANK.get(severity.lower(), 0) >= _SEVERITY_RANK.get(_MIN_SEVERITY, 2)


def post_security_alert(
    findings: list[dict],
    *,
    run_id: str = "",
    user: str = "anonymous",
) -> None:
    """Fire an alert for any findings that meet the minimum severity threshold.

    Safe to call with an empty list — returns immediately.
    """
    alertable = [f for f in findings if _should_alert(f.get("severity", "info"))]
    if not alertable:
        return

    logger.warning(
        "Security alert: %d finding(s) (run=%s user=%s): %s",
        len(alertable),
        run_id or "—",
        user,
        [f.get("code") for f in alertable],
    )

    if not _WEBHOOK_URL and not _PAGERDUTY_KEY:
        logger.info(
            "No SECURITY_WEBHOOK_URL or PAGERDUTY_ROUTING_KEY configured — "
            "alert logged only. Set one to enable push notifications."
        )
        return

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "run_id":    run_id,
        "user":      user,
        "findings":  alertable,
        "timestamp": timestamp,
    }

    if _WEBHOOK_URL:
        _post_webhook(payload)
    if _PAGERDUTY_KEY:
        _post_pagerduty(payload)


def _post_webhook(payload: dict) -> None:
    """POST to Slack Incoming Webhook, MS Teams webhook, or a generic URL."""
    try:
        import urllib.request
        url = _WEBHOOK_URL
        if "webhook.office.com" in url:
            # MS Teams adaptive-card format
            body = _teams_card(payload)
        else:
            # Slack / generic
            body = _slack_message(payload)

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_ALERT_TIMEOUT) as resp:  # noqa: S310
            status = resp.getcode()
            if status not in (200, 204):
                logger.warning("Security webhook returned HTTP %d", status)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Security webhook delivery failed: %s", exc)


def _post_pagerduty(payload: dict) -> None:
    """POST to PagerDuty Events API v2."""
    try:
        import urllib.request
        findings = payload["findings"]
        summary = f"[Backlog Synthesizer] {len(findings)} security finding(s): " + ", ".join(
            f.get("code", "?") for f in findings[:3]
        ) + ("…" if len(findings) > 3 else "")

        body = {
            "routing_key":  _PAGERDUTY_KEY,
            "event_action": "trigger",
            "payload": {
                "summary":   summary,
                "source":    "backlog-synthesizer",
                "severity":  "critical",
                "timestamp": payload["timestamp"],
                "custom_details": payload,
            },
        }
        req = urllib.request.Request(
            "https://events.pagerduty.com/v2/enqueue",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_ALERT_TIMEOUT) as resp:  # noqa: S310
            status = resp.getcode()
            if status != 202:
                logger.warning("PagerDuty Events API returned HTTP %d", status)
    except Exception as exc:  # noqa: BLE001
        logger.warning("PagerDuty delivery failed: %s", exc)


def post_synthesis_notification(
    *,
    run_id: str = "",
    user: str = "anonymous",
    source_label: str = "",
    epic_count: int = 0,
    story_count: int = 0,
    gap_count: int = 0,
    conflict_count: int = 0,
    elapsed_seconds: float = 0.0,
    cost_usd: float = 0.0,
) -> None:
    """Fire a synthesis-complete notification to Slack/Teams (SLACK_WEBHOOK_URL).

    Separate from security alerts — always fires on successful run completion.
    Silent if SLACK_WEBHOOK_URL is not set.
    """
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip() or _SLACK_NOTIFY_URL
    if not url:
        logger.debug("SLACK_WEBHOOK_URL not set — synthesis notification skipped.")
        return

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "run_id": run_id, "user": user, "source_label": source_label,
        "epic_count": epic_count, "story_count": story_count,
        "gap_count": gap_count, "conflict_count": conflict_count,
        "elapsed_seconds": elapsed_seconds, "cost_usd": cost_usd,
        "timestamp": timestamp,
    }
    try:
        import urllib.request
        if "webhook.office.com" in url:
            body = _teams_synthesis_card(payload)
        elif "logic.azure.com" in url or "powerplatform.com" in url:
            # MS Teams Workflows webhook (Power Automate / Power Platform) — plain JSON
            body = _teams_workflows_message(payload)
        else:
            body = _slack_synthesis_message(payload)
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_ALERT_TIMEOUT) as resp:  # noqa: S310
            if resp.getcode() not in (200, 204):
                logger.warning("Slack synthesis notification returned HTTP %d", resp.getcode())
            else:
                logger.info("Synthesis notification sent (run=%s)", run_id or "—")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Synthesis notification delivery failed: %s", exc)


def _slack_synthesis_message(p: dict) -> dict:
    lines = [
        f":white_check_mark: *Backlog Synthesizer — run complete*",
        f"*Source:* {p['source_label'] or '—'}   *User:* `{p['user']}`",
        f"*Epics:* {p['epic_count']}   *Stories:* {p['story_count']}   "
        f"*Gaps:* {p['gap_count']}   *Conflicts:* {p['conflict_count']}",
        f"*Elapsed:* {p['elapsed_seconds']:.1f}s   *Run ID:* `{p['run_id'] or '—'}`",
    ]
    return {"text": "\n".join(lines)}


def post_pipeline_failure_notification(
    *,
    user: str = "",
    source_label: str = "",
    error: str = "",
    partial: bool = False,
) -> None:
    """Fire a Slack/Teams notification when the pipeline fails or a stage errors."""
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip() or _SLACK_NOTIFY_URL
    if not url or not user:
        return

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "user": user, "source_label": source_label,
        "error": error, "partial": partial, "timestamp": timestamp,
    }
    try:
        import urllib.request
        if "webhook.office.com" in url:
            body = _teams_failure_card(payload)
        elif "logic.azure.com" in url or "powerplatform.com" in url:
            body = _teams_workflows_failure_message(payload)
        else:
            body = _slack_failure_message(payload)
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_ALERT_TIMEOUT) as resp:  # noqa: S310
            if resp.getcode() not in (200, 204):
                logger.warning("Failure notification returned HTTP %d", resp.getcode())
            else:
                logger.info("Pipeline failure notification sent (user=%s)", user)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pipeline failure notification delivery failed: %s", exc)


def _slack_failure_message(p: dict) -> dict:
    icon = ":warning:" if p["partial"] else ":red_circle:"
    label = "Partial failure" if p["partial"] else "Pipeline failed"
    return {"text": (
        f"{icon} *Backlog Synthesizer — {label}*\n"
        f"*Source:* {p['source_label'] or '—'}  |  *User:* `{p['user']}`\n"
        f"*Error:* {p['error']}"
    )}


def _teams_failure_card(p: dict) -> dict:
    label = "Partial failure" if p["partial"] else "Pipeline failed"
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": "FF8C00" if p["partial"] else "FF0000",
        "summary": f"Backlog Synthesizer — {label}",
        "sections": [{"activityTitle": f"Backlog Synthesizer — {label}", "facts": [
            {"name": "Source",  "value": p["source_label"] or "—"},
            {"name": "User",    "value": p["user"]},
            {"name": "Error",   "value": p["error"][:300]},
        ]}],
    }


def _teams_workflows_failure_message(p: dict) -> dict:
    icon = "⚠️" if p["partial"] else "🔴"
    label = "Partial failure" if p["partial"] else "Pipeline failed"
    return {"text": (
        f"{icon} **Backlog Synthesizer — {label}**\n\n"
        f"**Source:** {p['source_label'] or '—'}  |  **User:** {p['user']}\n\n"
        f"**Error:** {p['error']}"
    )}


def post_jira_push_notification(
    *,
    user: str = "",
    project: str = "",
    epic_count: int = 0,
    story_count: int = 0,
    subtask_count: int = 0,
) -> None:
    """Fire a Slack/Teams notification when stories are pushed to Jira."""
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip() or _SLACK_NOTIFY_URL
    if not url or not user:
        return

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "user": user, "project": project,
        "epic_count": epic_count, "story_count": story_count,
        "subtask_count": subtask_count, "timestamp": timestamp,
    }
    try:
        import urllib.request
        if "webhook.office.com" in url:
            body = _teams_jira_card(payload)
        elif "logic.azure.com" in url or "powerplatform.com" in url:
            body = _teams_workflows_jira_message(payload)
        else:
            body = _slack_jira_message(payload)
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_ALERT_TIMEOUT) as resp:  # noqa: S310
            if resp.getcode() not in (200, 204):
                logger.warning("Jira push notification returned HTTP %d", resp.getcode())
            else:
                logger.info("Jira push notification sent (user=%s project=%s)", user, project)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Jira push notification delivery failed: %s", exc)


def _slack_jira_message(p: dict) -> dict:
    return {"text": (
        f":rocket: *Backlog pushed to Jira — {p['project']}*\n"
        f"*{p['epic_count']}* epic(s)  ·  *{p['story_count']}* story(ies)  ·  *{p['subtask_count']}* sub-task(s)\n"
        f"Pushed by `{p['user']}`"
    )}


def _teams_jira_card(p: dict) -> dict:
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": "0052CC",
        "summary": f"Backlog pushed to Jira {p['project']}",
        "sections": [{"activityTitle": f"Backlog pushed to Jira — {p['project']}", "facts": [
            {"name": "Epics",     "value": str(p["epic_count"])},
            {"name": "Stories",   "value": str(p["story_count"])},
            {"name": "Sub-tasks", "value": str(p["subtask_count"])},
            {"name": "Pushed by", "value": p["user"]},
        ]}],
    }


def _teams_workflows_jira_message(p: dict) -> dict:
    return {"text": (
        f"📋 **Backlog pushed to Jira — {p['project']}**\n\n"
        f"**Epics:** {p['epic_count']}  |  **Stories:** {p['story_count']}  |  **Sub-tasks:** {p['subtask_count']}\n\n"
        f"**Pushed by:** {p['user']}"
    )}


def _teams_workflows_message(p: dict) -> dict:
    """Payload for MS Teams Workflows webhook (Power Automate / logic.azure.com).
    Uses a simple text body — the Workflow posts it as a channel message."""
    return {
        "text": (
            f"✅ **Backlog Synthesizer — run complete**\n\n"
            f"**Source:** {p['source_label'] or '—'}  |  **User:** {p['user']}\n\n"
            f"**Epics:** {p['epic_count']}  |  **Stories:** {p['story_count']}  |  "
            f"**Gaps:** {p['gap_count']}  |  **Conflicts:** {p['conflict_count']}\n\n"
            f"**Elapsed:** {p['elapsed_seconds']:.1f}s  |  **Run ID:** {p['run_id'] or '—'}"
        )
    }


def _teams_synthesis_card(p: dict) -> dict:
    facts = [
        {"name": "Source",    "value": p["source_label"] or "—"},
        {"name": "User",      "value": p["user"]},
        {"name": "Epics",     "value": str(p["epic_count"])},
        {"name": "Stories",   "value": str(p["story_count"])},
        {"name": "Gaps",      "value": str(p["gap_count"])},
        {"name": "Conflicts", "value": str(p["conflict_count"])},
        {"name": "Elapsed",   "value": f"{p['elapsed_seconds']:.1f}s"},
        {"name": "Run ID",    "value": p["run_id"] or "—"},
    ]
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": "6d28d9",
        "summary": "Backlog Synthesizer run complete",
        "sections": [{"activityTitle": "Backlog Synthesizer — Run Complete", "facts": facts}],
    }


def _slack_message(payload: dict) -> dict:
    findings = payload["findings"]
    lines = [
        f"*:rotating_light: Backlog Synthesizer — {len(findings)} security finding(s)*",
        f"Run: `{payload['run_id'] or '—'}`   User: `{payload['user']}`",
        "",
    ]
    for f in findings:
        icon = ":red_circle:" if f.get("severity") == "error" else ":warning:"
        lines.append(f"{icon} `{f.get('code', '?')}` — {f.get('message', '')}")
    return {"text": "\n".join(lines)}


def _teams_card(payload: dict) -> dict:
    findings = payload["findings"]
    facts = [
        {"name": "Run ID", "value": payload["run_id"] or "—"},
        {"name": "User",   "value": payload["user"]},
        {"name": "Findings", "value": str(len(findings))},
    ]
    for f in findings:
        facts.append({"name": f.get("code", "?"), "value": f.get("message", "")[:200]})
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": "FF0000",
        "summary": f"Security alert — {len(findings)} finding(s)",
        "sections": [{"activityTitle": "Backlog Synthesizer Security Alert", "facts": facts}],
    }
