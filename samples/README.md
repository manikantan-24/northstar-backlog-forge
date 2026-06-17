# Sample inputs

These files describe a single fictional product — **NorthStar Retail**, a national chain of ~2,000 supermarkets and big-box stores across the US — across the three input types the Backlog Synthesizer accepts.

The four source documents and two ticket exports cross-reference each other so the agents have genuine overlaps, conflicts, and gaps to find. A reviewer can verify the synthesis is correct by spot-checking these intentional flags.

## The NorthStar Retail fiction in one paragraph

NorthStar Retail runs about 2,000 stores nationwide, from urban grocery formats to suburban big-box destinations carrying grocery, electronics, apparel, home goods, pharmacy, and auto service. Engineering supports the point-of-sale lanes in every store, a consumer mobile app, an e-commerce site, the loyalty program, inventory and warehouse systems, pharmacy fulfillment (including the **Rx Hub** system of record for prescriptions), a vendor portal for ~50,000 suppliers, and store-associate tooling (handheld scanners running both legacy Android 7 hardware and a pilot Android 13 fleet).

## The files

| File | What it is | Notable details |
|---|---|---|
| `meeting_notes.txt` | Customer Experience Q3 planning meeting transcript | Five themes raised; one (self-checkout) explicitly declined; cross-references the architectural constraints in `architecture_constraints.md` and several existing JIRA tickets |
| `architecture_constraints.md` | Engineering architecture wiki page | Performance budgets, required integrations (NSID, PaymentGateway, Rx Hub), security rules (PCI, HIPAA, OFAC), offline tolerance per hardware tier, and forbidden patterns |
| `product_strategy.md` | Q3 strategy document from the VP of CX | Same themes as the meeting notes but formal; tags P0 vs P1; explicitly excludes self-checkout, vendor portal, B2B tier |
| `jira_backlog.json` | 30 existing JIRA tickets | Multiple intentional overlaps with the meeting notes (NS-412 search staleness, NS-419 mainframe scrape, NS-227 Kafka migration). Triggers RAG path (≥20 items). |
| `github_issues.json` | 6 existing GitHub issues | A second source of existing work; some overlap with JIRA, some unique (e.g., #1041 curbside GPS) |
| `vendor_security_proposal.md` | External vendor integration proposal | Contains 4 deliberate critical conflicts violating data residency, PCI-DSS card caching, custom crypto, and legacy mainframe access rules. |


## Intentional flags the agents should find

When the synthesizer runs against these inputs together, here is what a correct run should produce:

### Duplicates (new story ↔ existing ticket)

| Topic from meeting notes | Should be flagged as duplicate of | Confidence |
|---|---|---|
| Search returns out-of-stock items | `NS-412` (in-progress) + GitHub `#1247` | High |
| Mobile app polling for inventory | (n/a — this surfaces from constraints, see "gaps" below) | — |
| Loyalty tier confusion | `NS-389` (tier-downgrade email) + GitHub `#1102` | Medium-to-high |
| Curbside pickup wrong-store | GitHub `#1041` | High |

### Conflicts (story ↔ architecture constraint)

| Story idea | Conflicts with constraint | Severity |
|---|---|---|
| "Process card sales offline at the POS" — anyone proposing this | "Card sales when WAN is down: FORBIDDEN" (Section 4) | High |
| "Show personalized prices based on inventory state" | "Price personalization based on customer segment or inventory state requires Legal sign-off" (Section 3) | Medium |
| "Use BLE central role on handheld for inventory beacons" | "Legacy Android 7 handheld cannot use BLE central role" (Section 4) | High if it doesn't gate by hardware |

### Gaps (implied but missing from both new stories and existing backlog)

The agents should also surface things the strategy/transcript *implies* but neither the new stories nor existing tickets cover. Examples a reviewer should expect:

- HIPAA opt-in capture flow (the strategy mentions opt-in pharmacy notifications but no story addresses *how* the patient opts in)
- WAN-failure detection and switchover heuristics for the POS (offline mode is mentioned but the *trigger* for going offline isn't designed)
- The audit log retention extension (existing NS-321 covers this — so this is actually NOT a gap; the agent should recognize it's covered)

## Sample sizes and threshold behavior

- `jira_backlog.json` has **30 tickets**, which is above the `RETRIEVAL_THRESHOLD=20` in `src/memory/store.py`. This triggers the embedding-based semantic search path.
- `github_issues.json` has only 6, so they're included in the LLM prompt directly without retrieval narrowing.

## How each sample is designed to be used

```bash
# Full run with all three sources:
python src/main.py \
    --transcript samples/meeting_notes.txt \
    --constraints samples/architecture_constraints.md \
    --backlog samples/jira_backlog.json

# Strategy document instead of meeting notes:
python src/main.py \
    --transcript samples/product_strategy.md \
    --constraints samples/architecture_constraints.md \
    --backlog samples/jira_backlog.json

# Smaller demo with no wiki:
python src/main.py --transcript samples/meeting_notes.txt
```
