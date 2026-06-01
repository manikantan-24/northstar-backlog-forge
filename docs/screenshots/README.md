# Screenshots & demo media

These images are referenced by the top-level `README.md` ("Demo" section) and were **captured from a real headless run** of the app against the bundled sample (Balanced preset, 30-ticket backlog so the RAG path engages):

- `01_home.png` — landing / empty state
- `02_pipeline.png` — the five agents mid-run
- `03_epics.png` — completed run: pipeline + KPI cards + Epics tab
- `04_findings.png` — duplicates / conflicts / gaps
- `05_audit.png` — the audit trail

## Regenerating them

They were produced by driving the live UI with Playwright + the system Chrome. To refresh after a UI change:

```bash
pip install playwright                       # browsers: uses system Chrome via channel="chrome"
make ui                                       # in one terminal (or launch headless on :8501)
# then run a capture script that loads http://127.0.0.1:8501, clicks Synthesize,
# waits for the result tabs, and screenshots each — full_page=True at 1680x1050.
```

You can also just recapture by hand (macOS `Cmd-Shift-4`) and overwrite the files.

## Optional extra shots

- `06_cost.png` — expand the **Cost & tokens** panel (visible in `03_epics.png`) for a dedicated per-agent cost view.
- `demo.gif` — a 30–60s screen recording of a full run; drop it here and uncomment the line in the README's Demo section.

## Original shot-list reference (filenames the README expects)

| Filename | What to capture | Why it sells |
|---|---|---|
| `01_home.png` | The landing screen — 3-step explainer + the SYNTHESIZE call-to-action | First impression; shows it's a polished product |
| `02_pipeline.png` | The five-stage pipeline mid-run (a stage lit "active", others done) | Proves the multi-agent architecture visually |
| `03_epics.png` | The Epics tab — an epic expanded to stories → tasks, with the evidence blockquote | Shows the structured output + traceability |
| `04_findings.png` | The duplicates / conflicts / gaps panel (a duplicate-compare modal if you have one) | The "beyond a single prompt" differentiator |
| `05_audit.png` | The audit trail tab with one agent's prompt+response expanded | Auditability — a rubric requirement |
| `06_cost.png` | The cost panel (per-agent tokens + cost, trend chart) | Cost-awareness; engineering maturity |

## Optional but high-impact

- `demo.gif` — a 30–60s screen recording of a full run (record with `Cmd-Shift-5`, convert to GIF). Drop it in here and the README's Demo section will feature it at the top.
- `architecture.png` — a rendered image of the Mermaid diagram in [`../../architecture.md`](../../architecture.md). Paste the diagram into <https://mermaid.live>, export PNG, and save here.
