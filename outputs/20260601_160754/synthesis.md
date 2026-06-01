# Backlog Synthesis

*Synthesized from: product_strategy.md*

## Summary

Q3 strategy document defining five customer-experience initiatives across POS, pharmacy, mobile search, loyalty, and store hardware. Three P0 items must ship by end of Q3; two P1 items are stretch goals. Document requests engineering to synthesize into structured backlog, cross-check existing work, and surface gaps and conflicts.

## Epics (5)

### Epic 1: Point-of-Sale Offline Resilience

Enable POS lanes to handle critical transaction types during WAN outages, reducing revenue loss and customer friction while maintaining PCI compliance for payment card data.

#### 1.1 Enable offline cash sales, gift card redemption, and small returns at POS

**Priority:** High   |   **Tags:** `pos` `offline-mode` `payments` `compliance`

> POS lanes become completely unavailable during WAN outages, blocking even cash transactions. Three Houston stores lost an estimated $42K in direct revenue during a 40-minute outage in March. This story enables cash sales, gift card redemption, and returns under $50 to function offline while keeping card sales online-only per PCI. Aligns with constraints C-01, C-02, C-03, and C-04.

**User story**
- As a store associate, I want to process cash sales, gift card redemptions, and small returns during WAN outages, so that customers can complete transactions and we don't lose revenue during connectivity issues.

**Acceptance criteria**
- Given a WAN outage at a POS lane, when an associate attempts a cash sale, then the transaction completes successfully and is reconciled when connectivity returns.
- Given a WAN outage at a POS lane, when an associate attempts a gift card redemption, then the transaction completes successfully and is reconciled when connectivity returns.
- Given a WAN outage at a POS lane, when an associate attempts a return under $50, then the transaction completes successfully and is reconciled when connectivity returns.
- Given a WAN outage at a POS lane, when an associate attempts a card sale, then the system displays a clear message that card sales require online connectivity per PCI requirements.
- Given connectivity is restored, when offline transactions exist, then each is reconciled exactly once with full audit trail.

**Tasks**
- ST-01-TK-01: Implement local transaction queue with persistence for cash, gift card, and return transactions during offline mode
- ST-01-TK-02: Build reconciliation service to process queued offline transactions when connectivity is restored with idempotency guarantees
- ST-01-TK-03: Add POS client logic to detect WAN outage state and route eligible transaction types to offline queue
- ST-01-TK-04: Create audit trail schema and persistence layer for offline transactions with full lifecycle tracking
- ST-01-TK-05: Implement UI messaging to inform associates when card sales are blocked during offline mode with clear PCI rationale
- ST-01-TK-06: Build automated integration tests covering offline transaction creation, queue persistence, and post-restoration reconciliation

#### 1.2 Enable offline card sales at POS during WAN outages

**Priority:** Low   |   **Tags:** `pos` `offline-mode` `payments` `compliance`

> A question was raised about enabling card sales during offline mode alongside cash sales. This capability is explicitly blocked by C-04 (PCI compliance requires card sales remain online-only). This story is drafted to surface the requested capability and the conflict for review, as the constraint creates a gap between desired offline resilience and payment security requirements. Would require Architecture Review Board sign-off per C-16 and Legal + InfoSec compliance review per C-15 if pursued.

**User story**
- As a store associate, I want to process card sales during WAN outages, so that we don't lose revenue when connectivity drops.

**Acceptance criteria**
- Given a WAN outage at a POS lane, when an associate attempts a card sale, then the transaction is either completed within PCI requirements or clearly rejected with an explanation.
- Given card transactions are queued offline, when connectivity is restored, then each transaction is reconciled exactly once with full PCI-compliant audit trail.
- Given offline card sales are enabled, when compliance review is completed, then Legal and InfoSec have approved the implementation under PCI requirements.

**Tasks**
- ST-06-TK-01: Conduct spike to identify PCI-compliant offline card payment architectures used by other large retailers
- ST-06-TK-02: Document technical requirements and compliance boundaries for offline card tokenization or store-and-forward patterns
- ST-06-TK-03: Prepare Architecture Review Board proposal with compliance impact assessment and risk mitigation plan
- ST-06-TK-04: Engage Legal and InfoSec for preliminary feasibility review of offline card processing under current PCI certification

---

### Epic 2: Pharmacy Experience Unification

Consolidate fragmented pharmacy refill channels into a single system of record to eliminate customer confusion, reduce help line load, and ensure HIPAA-compliant notifications.

#### 2.1 Unify mobile app and IVR refill requests to write to Rx Hub

**Priority:** High   |   **Tags:** `pharmacy` `mobile-app` `compliance`

> Mobile app and IVR refill requests currently write to separate systems with no real-time reconciliation, causing customers to arrive expecting prescriptions that aren't ready. The pharmacy help line spends 18% of its volume disambiguating status. This story unifies both channels to write to Rx Hub as the system of record with HIPAA-compliant status notifications. Must comply with C-05, C-06, C-07, C-08, C-13, and requires Legal + InfoSec compliance review before implementation.

**User story**
- As a pharmacy customer, I want my refill requests from mobile app or phone to be processed in a single system, so that I receive accurate status updates and my prescription is ready when I arrive.

**Acceptance criteria**
- Given a customer requests a refill via mobile app, when the request is submitted, then it writes to Rx Hub as the system of record.
- Given a customer requests a refill via IVR phone line, when the request is submitted, then it writes to Rx Hub as the system of record.
- Given a refill request is received, when the prescription status changes, then a HIPAA-compliant notification is sent only to the patient's verified contact method (not household default) if opt-in is stored on the prescription.
- Given a refill notification is sent, when the audit log is created, then it is retained for 7 years per HIPAA requirements.
- Given both channels are writing to Rx Hub, when the pharmacy help line receives a status inquiry, then accurate real-time status is available from a single source.

**Tasks**
- ST-02-TK-01: Refactor mobile app refill submission to write directly to Rx Hub API instead of legacy system
- ST-02-TK-02: Refactor IVR refill submission to write directly to Rx Hub API instead of legacy system
- ST-02-TK-03: Implement HIPAA-compliant notification service that sends status updates only to patient-verified contact methods with explicit opt-in
- ST-02-TK-04: Create audit log schema with 7-year retention policy for all refill notifications and status changes
- ST-02-TK-05: Update pharmacy help line tools to query unified Rx Hub status endpoint for real-time prescription data
- ST-02-TK-06: Configure audit log retention policy and encryption controls to meet HIPAA 7-year requirement
- ST-02-TK-07: Build regression test suite covering mobile app, IVR, and notification flows with HIPAA compliance validation

---

### Epic 3: Mobile App Inventory Intelligence

Enhance mobile app search and product discovery by integrating real-time local inventory data to reduce customer frustration and improve conversion rates.

#### 3.1 Rerank mobile search results by home store availability

**Priority:** High   |   **Tags:** `mobile-app` `inventory` `performance`

> Mobile app search does not factor local inventory, leading customers to drive to stores for items that are out of stock. NPS for affected customers is -30 versus +18 for unaffected customers. This story reranks search results by home store availability and surfaces in-stock alternatives inline when top results are unavailable locally. Must stay under the 800ms p95 search latency budget (C-10). Must not personalize pricing based on inventory state without disclosure per C-09 and C-14.

**User story**
- As a mobile app customer, I want search results ranked by my home store's availability, so that I don't drive to the store for items that are out of stock.

**Acceptance criteria**
- Given a customer has set a home store, when they search for a product, then results are reranked with in-stock items at their home store appearing higher than out-of-stock items.
- Given a customer searches for a product, when the top results are out of stock at their home store, then in-stock alternatives are surfaced inline.
- Given the search reranking is implemented, when search operations execute, then p95 latency remains under 800ms.
- Given results are reranked by inventory, when pricing is displayed, then prices are not personalized based on inventory state without explicit disclosure.
- Given a customer views search results, when inventory status is shown, then it reflects real-time or near-real-time data for their selected home store.

**Tasks**
- ST-03-TK-01: Build inventory lookup service that retrieves real-time stock levels by store and product with caching for performance
- ST-03-TK-02: Implement search result reranking algorithm that boosts in-stock items at customer's home store while maintaining relevance
- ST-03-TK-03: Add inline alternative product suggestion logic when top results are out of stock locally
- ST-03-TK-04: Update mobile app search UI to display inventory status and alternative suggestions inline with search results
- ST-03-TK-05: Implement performance monitoring and alerting to ensure p95 search latency stays under 800ms threshold
- ST-03-TK-06: Add validation checks to prevent pricing personalization based on inventory state without explicit disclosure
- ST-03-TK-07: Build load tests simulating search traffic with inventory lookups to validate latency budget under production conditions

---

### Epic 4: Loyalty Program Transparency and Engagement

Improve customer understanding and engagement with the loyalty program by surfacing tier progress and evaluation timelines directly in the mobile app.

#### 4.1 Add loyalty tier progress view in mobile app

**Priority:** Medium   |   **Tags:** `loyalty` `mobile-app`

> Customers do not understand loyalty tier earning and downgrade rules, leading to support contacts. This story adds a tier progress view in the mobile app showing current points, distance to next tier, and next evaluation date. Must declare hardware floor and provide explicit fallbacks for legacy Android 7 handheld fleet per C-11, and should target Android 9+ baseline per C-12.

**User story**
- As a loyalty program member, I want to see my tier progress in the mobile app, so that I understand how close I am to the next tier and when my tier will be re-evaluated.

**Acceptance criteria**
- Given a customer opens the loyalty section in the mobile app, when they view tier progress, then current points, distance to next tier, and next evaluation date are clearly displayed.
- Given a customer is approaching a tier downgrade, when they view tier progress, then the next evaluation date and risk of downgrade are clearly communicated.
- Given the feature is deployed, when the hardware floor is documented, then it explicitly states Android 9+ baseline with defined fallbacks for Android 7 legacy fleet.
- Given a customer on Android 7 hardware accesses the app, when tier progress is unavailable, then a clear message explains the hardware limitation and provides alternative access methods.

**Tasks**
- ST-04-TK-01: Build loyalty tier progress API endpoint that calculates current points, next tier threshold, and evaluation date
- ST-04-TK-02: Implement mobile app tier progress UI screen with progress visualization and evaluation timeline for Android 9+
- ST-04-TK-03: Add downgrade warning messaging when customer is at risk based on evaluation date and point trajectory
- ST-04-TK-04: Create graceful degradation fallback for Android 7 devices with clear messaging and alternative access instructions
- ST-04-TK-05: Document hardware floor requirements (Android 9+ baseline, Android 7 fallback) in feature specification and deployment guide
- ST-04-TK-06: Build automated tests covering tier progress calculation accuracy and UI rendering on multiple Android versions

---

### Epic 5: Mobile Platform Standards and Governance

Establish engineering discipline and process controls to manage hardware constraints and ensure consistent mobile app delivery across diverse device fleets.

#### 5.1 Establish hardware floor declaration discipline for mobile app stories

**Priority:** Medium   |   **Tags:** `mobile-app` `store-associate` `platform`

> Existing 7-year-old store associate handhelds run Android 7, limiting new tooling capabilities. Hardware refresh is approved for FY26 Q1 but not yet deployed. This story establishes a discipline requiring every new mobile-app story to declare its hardware floor and provide explicit fallbacks for the legacy Android 7 fleet, per C-11 and C-12. This is a process/discipline story rather than a feature delivery.

**User story**
- As an agile delivery lead, I want every new mobile-app story to declare its hardware floor and legacy fallbacks, so that we avoid deploying features that break on the legacy Android 7 handheld fleet.

**Acceptance criteria**
- Given a new mobile-app story is drafted, when it enters the backlog, then it includes an explicit hardware floor declaration (e.g., Android 9+).
- Given a story declares a hardware floor above Android 7, when it enters the backlog, then it includes documented fallbacks or graceful degradation for the Android 7 legacy fleet.
- Given the discipline is established, when story templates are updated, then they include a mandatory hardware floor field.
- Given a story is reviewed in refinement, when the hardware floor is missing or incomplete, then the story is returned for revision before acceptance.

**Tasks**
- ST-05-TK-01: Create story template update that adds mandatory hardware floor declaration field with validation rules
- ST-05-TK-02: Document hardware floor policy in engineering playbook with examples and fallback pattern guidance
- ST-05-TK-03: Update backlog refinement checklist to include hardware floor completeness validation as acceptance gate
- ST-05-TK-04: Conduct team training session on hardware floor requirements and Android version compatibility considerations

---

## 🔍 Gaps detected

Capabilities implied by the source material that are not represented in the existing backlog.

- **Offline transaction reconciliation and sync after WAN recovery** — ST-01 enables offline cash sales, gift card redemption, and small returns during WAN outages, but no story or existing ticket addresses how these queued transactions reconcile and sync back to central systems once connectivity returns, which matters for inventory accuracy, financial reporting, and audit compliance.
  - *Evidence:* ST-01 description states 'enables cash sales, gift card redemption, and returns under $50 to function offline' during outages, but does not describe the reconciliation mechanism after WAN recovery.
- **IVR refill request patient identity verification under HIPAA** — ST-02 unifies mobile app and IVR refill requests to write to Rx Hub with HIPAA-compliant notifications, but neither this story nor existing pharmacy tickets explicitly address how IVR callers are authenticated to ensure refill requests meet the C-07 requirement that notifications go only to the patient's verified contact method.
  - *Evidence:* C-07 states notifications must be 'sent to the patient's verified contact (not household default)', but ST-02 does not describe IVR caller verification mechanisms to ensure HIPAA compliance for automated phone requests.
- **Mobile search result reranking fallback when inventory service is unavailable** — ST-03 reranks mobile search results by home store availability to reduce customer disappointment, but does not address the fallback behavior when the inventory service is unavailable or slow, which matters for meeting the C-10 800ms p95 latency budget and maintaining search functionality during degraded conditions.
  - *Evidence:* ST-03 states search results will be reranked by home store availability and must stay under 800ms p95 latency per C-10, but does not specify what happens when inventory data is unavailable or times out.

## ⚠️ Conflicts

New stories that contradict architectural constraints or in-flight work.

- **Story ST-06** conflicts with **C-04** (severity: high)
  - The story proposes enabling offline card sales during WAN outages, which directly contradicts the constraint that card sales must remain online-only per PCI requirements.

## ♻️ Possible duplicates

New stories that overlap with existing JIRA / GitHub tickets.

- **Story ST-02** overlaps with **NS-11** (confidence: low)
  - Local-embedding cosine similarity 0.66 — new story title/description overlaps existing ticket "Pharmacy refill: SMS reminder when rx is due".
- **Story ST-02** overlaps with **NS-53** (confidence: low)
  - Local-embedding cosine similarity 0.65 — new story title/description overlaps existing ticket "Pharmacy refill: SMS reminder when rx is due".
