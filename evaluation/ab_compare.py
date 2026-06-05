"""A/B compare two prompt variants on the same golden dataset.

Use this when you want to know whether a tweaked prompt is actually
better. The harness:

  1. Snapshots the current prompt file (the "control") to a temp location.
  2. Overwrites it with the candidate "variant" file.
  3. Runs the evaluation on the chosen cases.
  4. Restores the original prompt.
  5. Compares variant vs. control scores per case and in aggregate.

Both runs reuse the same orchestrator/golden dataset, so the only
difference is the prompt under test. Restoring the original prompt is
done in a `finally:` block so an interrupted run doesn't leave the repo
in a half-applied state.

Usage:
    python evaluation/ab_compare.py \\
        --prompt parser_prompt.md \\
        --variant prompts/experiments/parser_prompt_v2.md \\
        [--case case_01] [--use-llm-judge] [--label v2-fewer-rules]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evaluation"))

PROMPTS_DIR = ROOT / "prompts"
RESULTS_DIR = ROOT / "evaluation" / "results"


def _run_evaluation(cases: list[str], use_llm_judge: bool) -> list[dict]:
    """Invoke the evaluation runner programmatically and return raw results."""
    from run_evaluation import run_case, list_cases
    case_ids = cases or list_cases()
    out: list[dict] = []
    for cid in case_ids:
        try:
            out.append(run_case(cid, use_llm_judge=use_llm_judge))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] case {cid} failed: {e}", file=sys.stderr)
    return out


def _aggregate(results: list[dict]) -> dict[str, float | None]:
    if not results:
        return {"det": None, "judge": None}
    det = sum(r["deterministic_average"] for r in results) / len(results)
    judge_results = [r for r in results if r.get("llm_judge")]
    judge = (
        sum(r["llm_judge"]["average_normalized"] for r in judge_results)
        / len(judge_results)
        if judge_results else None
    )
    return {"det": det, "judge": judge}


def _compare(control: list[dict], variant: list[dict]) -> list[dict]:
    """Per-case diff between control and variant scores."""
    by_id_v = {r["case_id"]: r for r in variant}
    rows: list[dict] = []
    for cr in control:
        cid = cr["case_id"]
        vr = by_id_v.get(cid)
        if not vr:
            continue
        row: dict[str, object] = {
            "case_id": cid,
            "control_det": cr["deterministic_average"],
            "variant_det": vr["deterministic_average"],
            "det_delta": vr["deterministic_average"] - cr["deterministic_average"],
        }
        if cr.get("llm_judge") and vr.get("llm_judge"):
            row["control_judge"] = cr["llm_judge"]["average_normalized"]
            row["variant_judge"] = vr["llm_judge"]["average_normalized"]
            row["judge_delta"] = (
                vr["llm_judge"]["average_normalized"]
                - cr["llm_judge"]["average_normalized"]
            )
        rows.append(row)
    return rows


def _verdict(rows: list[dict]) -> str:
    """One-line summary: did the variant win, lose, or tie?"""
    if not rows:
        return "no comparable cases"
    deltas = [r["det_delta"] for r in rows if isinstance(r.get("det_delta"), (int, float))]
    if not deltas:
        return "no deterministic deltas computed"
    mean = sum(deltas) / len(deltas)
    if mean > 0.02:
        return f"variant WINS  (mean Δ {mean:+.3f})"
    if mean < -0.02:
        return f"control WINS  (mean Δ {mean:+.3f})"
    return f"approximately TIE  (mean Δ {mean:+.3f})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True,
                        help="Filename inside prompts/ to A/B test (e.g. parser_prompt.md)")
    parser.add_argument("--variant", required=True,
                        help="Path to the candidate prompt file to compare against the control")
    parser.add_argument("--case", default=None,
                        help="Limit to a single case (default: all)")
    parser.add_argument("--use-llm-judge", action="store_true",
                        help="Include qualitative judge scores in the comparison")
    parser.add_argument("--label", default=None,
                        help="Human label for the variant — used in the report filename")
    args = parser.parse_args()

    prompt_path = PROMPTS_DIR / args.prompt
    variant_path = Path(args.variant)
    if not prompt_path.exists():
        print(f"Control prompt not found: {prompt_path}", file=sys.stderr)
        return 1
    if not variant_path.exists():
        print(f"Variant prompt not found: {variant_path}", file=sys.stderr)
        return 1

    cases = [args.case] if args.case else []
    label = args.label or variant_path.stem

    # --- 1. Control run with the prompt currently on disk
    print("=" * 70)
    print(f" A/B compare — control: {args.prompt}")
    print(f"             variant: {variant_path}  (label: {label})")
    print(f"             use_llm_judge: {args.use_llm_judge}")
    print("=" * 70)

    print("\n>> Running CONTROL (existing prompt)...")
    control_results = _run_evaluation(cases, use_llm_judge=args.use_llm_judge)

    # --- 2. Variant run — swap in the candidate prompt, run, restore
    snapshot = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(prompt_path.read_text(encoding="utf-8"))
            snapshot = Path(tmp.name)

        print(f"\n>> Swapping in variant from {variant_path}")
        prompt_path.write_text(variant_path.read_text(encoding="utf-8"), encoding="utf-8")

        # The orchestrator and agents read prompts at construction time, so a
        # fresh import here would still see the new file — but to be safe
        # against any future module-level caching, drop cached modules.
        for mod in ("orchestrator", "agents.parser_agent", "agents.story_writer_agent",
                    "agents.constraint_agent", "agents.epic_decomposer_agent",
                    "agents.gap_detector_agent", "run_evaluation"):
            sys.modules.pop(mod, None)

        print("\n>> Running VARIANT...")
        variant_results = _run_evaluation(cases, use_llm_judge=args.use_llm_judge)
    finally:
        if snapshot is not None and snapshot.exists():
            prompt_path.write_text(snapshot.read_text(encoding="utf-8"), encoding="utf-8")
            snapshot.unlink()
            print(f"\n>> Restored control prompt {args.prompt}")

    # --- 3. Compare + report
    rows = _compare(control_results, variant_results)
    agg_c = _aggregate(control_results)
    agg_v = _aggregate(variant_results)

    print("\n" + "=" * 70)
    print(" A/B Results")
    print("=" * 70)
    print(f"\n  Control deterministic avg: {agg_c['det']:.3f}"
          if agg_c['det'] is not None else "\n  Control deterministic avg: —")
    print(f"  Variant deterministic avg: {agg_v['det']:.3f}"
          if agg_v['det'] is not None else "  Variant deterministic avg: —")
    if args.use_llm_judge:
        if agg_c['judge'] is not None:
            print(f"  Control judge avg (norm):  {agg_c['judge']:.3f}")
        if agg_v['judge'] is not None:
            print(f"  Variant judge avg (norm):  {agg_v['judge']:.3f}")
    print(f"\n  Verdict: {_verdict(rows)}\n")

    print(f"  {'case_id':<14s}  {'ctrl_det':>9s}  {'var_det':>9s}  {'Δ_det':>8s}")
    print(f"  {'-' * 14}  {'-' * 9}  {'-' * 9}  {'-' * 8}")
    for r in rows:
        print(f"  {r['case_id']:<14s}  "
              f"{r['control_det']:>9.3f}  "
              f"{r['variant_det']:>9.3f}  "
              f"{r['det_delta']:>+8.3f}")

    # --- 4. Persist
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_dir = RESULTS_DIR / "ab" / f"{ts}_{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps({
        "control_prompt": args.prompt,
        "variant_prompt": str(variant_path),
        "label": label,
        "use_llm_judge": args.use_llm_judge,
        "control_aggregate": agg_c,
        "variant_aggregate": agg_v,
        "per_case": rows,
        "verdict": _verdict(rows),
    }, indent=2, default=str))
    print(f"\n  Report written to: {out_dir}/report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
