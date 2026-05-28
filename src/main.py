"""Backlog Synthesizer — CLI entry point.

Reads a transcript (+ optional wiki + optional ticket export), runs the
multi-agent orchestrator, writes synthesis.json + synthesis.md + audit_trail.md
into a timestamped subdirectory under outputs/.

Usage:
    python src/main.py \
        --transcript samples/meeting_notes.txt \
        --constraints samples/architecture_constraints.md \
        --backlog samples/jira_backlog.json
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Load .env so ANTHROPIC_API_KEY is available without shell exports
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Same Atlassian tenant uses one API token for both Jira and Confluence.
# Promote JIRA_* into CONFLUENCE_* so users only have to maintain one set.
for _conf, _jira in (
    ("CONFLUENCE_BASE_URL", "JIRA_BASE_URL"),
    ("CONFLUENCE_EMAIL", "JIRA_EMAIL"),
    ("CONFLUENCE_API_TOKEN", "JIRA_API_TOKEN"),
):
    if not os.environ.get(_conf) and os.environ.get(_jira):
        os.environ[_conf] = os.environ[_jira]

from logger_setup import get_logger  # noqa: E402
from input_loader import load_text, load_tickets, InputError  # noqa: E402
from output_formatter import write_outputs  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synthesize epics + stories + tasks from transcripts, wikis, and existing tickets."
    )
    parser.add_argument(
        "--transcript", "-t",
        required=True,
        help="Path to a meeting transcript / requirement document (.txt, .md, .pdf)",
    )
    parser.add_argument(
        "--constraints", "-c",
        default=None,
        help="Optional path to an architecture / wiki export (.md) describing constraints",
    )
    parser.add_argument(
        "--backlog", "-b",
        default=None,
        help="Optional path to an existing JIRA or GitHub ticket export (.json)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="outputs",
        help="Directory to write outputs to (default: outputs/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load inputs but don't call the API; print what would be sent.",
    )
    parser.add_argument(
        "--redact-pii",
        action="store_true",
        help=(
            "Replace emails, phone numbers, claim/policy/case IDs, SSNs, card "
            "numbers, and conservatively-matched personal names with stable "
            "placeholders BEFORE sending to the LLM. The synthesis is un-redacted "
            "in the final output; the audit log stays redacted for compliance."
        ),
    )
    parser.add_argument(
        "--confluence-page-id",
        default=None,
        help=(
            "Confluence page id to pull architecture constraints from in live mode. "
            "Requires CONFLUENCE_BASE_URL / CONFLUENCE_EMAIL / CONFLUENCE_API_TOKEN "
            "(or the JIRA_* equivalents on the same tenant). Overrides --constraints."
        ),
    )
    parser.add_argument(
        "--live-jira",
        action="store_true",
        help=(
            "Pull existing tickets from live Jira (project = JIRA_PROJECT_KEY) "
            "instead of --backlog. Requires JIRA_BASE_URL / JIRA_EMAIL / "
            "JIRA_API_TOKEN."
        ),
    )
    parser.add_argument(
        "--vision-image",
        action="append",
        default=None,
        help=(
            "Attach a PNG/JPG/WEBP/GIF image (e.g. a whiteboard photo) "
            "as additional source material. Vision-capable models only "
            "(Claude Sonnet/Opus/Haiku 4.x). Can be repeated."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # ---- Load inputs ----
    try:
        logger.info("Loading transcript from %s", args.transcript)
        transcript_text = load_text(args.transcript)
        logger.info("Loaded %d chars of transcript", len(transcript_text))
    except InputError as e:
        logger.error("Could not read transcript: %s", e)
        return 2

    constraint_text = ""
    if args.constraints:
        try:
            logger.info("Loading constraints from %s", args.constraints)
            constraint_text = load_text(args.constraints)
            logger.info("Loaded %d chars of constraints", len(constraint_text))
        except InputError as e:
            logger.warning("Could not load constraints (continuing without): %s", e)

    existing_tickets: list[dict] = []
    if args.backlog:
        try:
            logger.info("Loading backlog from %s", args.backlog)
            existing_tickets = load_tickets(args.backlog)
            logger.info("Loaded %d existing tickets", len(existing_tickets))
        except InputError as e:
            logger.warning("Could not load backlog (continuing without): %s", e)

    if args.dry_run:
        print(f"[dry-run] Transcript chars: {len(transcript_text)}")
        print(f"[dry-run] Constraint chars: {len(constraint_text)}")
        print(f"[dry-run] Existing tickets: {len(existing_tickets)}")
        return 0

    # ---- Run the multi-agent orchestrator ----
    # Live-mode hints flow through to Orchestrator.run; the orchestrator
    # records both success and failure of the fetch in the audit log so
    # the run is reproducible after the fact.
    if args.confluence_page_id and args.constraints:
        logger.warning(
            "Both --constraints and --confluence-page-id given; "
            "--confluence-page-id wins."
        )
        constraint_text = ""  # let live fetch fill it
    if args.live_jira and args.backlog:
        logger.warning(
            "Both --backlog and --live-jira given; --live-jira wins."
        )
        existing_tickets = []  # let live fetch fill it

    # Build vision attachments if any --vision-image paths were passed.
    vision_attachments = None
    if args.vision_image:
        from tools.base import VisionAttachment, ToolError as _ToolError
        try:
            vision_attachments = [VisionAttachment.from_path(p) for p in args.vision_image]
        except _ToolError as e:
            logger.error("Could not load vision image: %s", e)
            return 2

    try:
        orchestrator = Orchestrator()
        result = orchestrator.run(
            transcript_text=transcript_text,
            constraint_text=constraint_text,
            existing_tickets=existing_tickets,
            redact_pii=args.redact_pii,
            live_confluence_page_id=args.confluence_page_id,
            live_jira=args.live_jira,
            vision_attachments=vision_attachments,
        )
    except Exception as e:
        logger.error("Orchestrator failed unexpectedly: %s", e)
        return 3

    # ---- Write outputs ----
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.output_dir) / stamp
    json_path, md_path = write_outputs(
        {k: v for k, v in result.items() if k != "audit_trail"},
        run_dir,
        source_label=args.transcript,
    )
    audit_path = run_dir / "audit_trail.md"
    audit_path.write_text(result["audit_trail"], encoding="utf-8")

    # Console summary
    print()
    print("=" * 70)
    print(f"  Transcript : {args.transcript}")
    if args.confluence_page_id:
        print(f"  Constraints: live Confluence page {args.confluence_page_id}")
    else:
        print(f"  Constraints: {args.constraints or '(none)'}")
    if args.live_jira:
        print(f"  Backlog    : live Jira (project = {os.environ.get('JIRA_PROJECT_KEY') or '?'})")
    else:
        print(f"  Backlog    : {args.backlog or '(none)'}")
    print(f"  Epics      : {len(result.get('epics', []))}")
    n_stories = sum(len(e.get('stories', [])) for e in result.get('epics', []))
    print(f"  Stories    : {n_stories}")
    print(f"  Gaps       : {len(result.get('gaps', []))}")
    print(f"  Conflicts  : {len(result.get('conflicts', []))}")
    print(f"  Duplicates : {len(result.get('duplicates', []))}")
    print(f"  Synthesis  : {md_path}")
    print(f"  Audit trail: {audit_path}")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
