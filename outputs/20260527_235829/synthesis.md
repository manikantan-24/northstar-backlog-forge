# Backlog Synthesis

*Synthesized from: meeting_notes.txt*

## Summary

Q3 planning meeting focused on five customer-facing problems surfaced through store manager feedback and in-app surveys. Primary themes were POS system resilience during network outages, mobile app search accuracy with inventory, pharmacy refill system fragmentation, loyalty tier transparency, and technical constraints from aging store hardware.

## Epics (5)

### Epic 1: POS Offline Resilience

Enable point-of-sale systems to continue processing critical transaction types during WAN outages, preventing revenue loss and customer abandonment while maintaining payment security and data synchronization requirements.

#### 1.1 Enable offline cash sales and returns at POS during WAN outages

**Priority:** High   |   **Tags:** `pos` `offline-mode` `payments`

> POS systems currently prevent all transactions during WAN outages, including cash sales. This causes direct revenue loss and forces store managers to turn away customers during peak hours. Solution must enable local SKU pricing validation for cash, gift card redemption, and sub-$50 returns while maintaining card-payment-online-only requirement.

**User story**
- As a store cashier, I want to process cash sales, gift card redemptions, and small returns during internet outages, so that we don't lose revenue or turn away customers when the WAN connection drops.

**Acceptance criteria**
- Given a WAN outage at a store, when a cashier scans a SKU for a cash sale, then the POS validates pricing from local cache and completes the transaction.
- Given a WAN outage, when a customer attempts to pay with a credit or debit card, then the POS displays a clear error message that card payments are unavailable and suggests cash or gift card.
- Given a WAN outage, when a cashier processes a gift card redemption, then the transaction completes using local gift card balance validation.
- Given a WAN outage, when a cashier processes a return under $50, then the transaction completes with local receipt validation.
- Given WAN connectivity is restored, when the POS reconnects, then all offline transactions sync to the central system within 5 minutes with conflict resolution for any SKU price changes.

**Tasks**
- ST-01-TK-01: Implement local SKU pricing cache with TTL management and delta sync protocol for POS terminals.
- ST-01-TK-02: Add offline transaction queue with persistent storage and retry logic on POS client.
- ST-01-TK-03: Build gift card balance validation service using local read replica with write-back on reconnect.
- ST-01-TK-04: Create receipt validation logic for sub-$50 returns using local transaction history lookup.
- ST-01-TK-05: Implement conflict resolution handler for SKU price changes during offline window with audit trail.
- ST-01-TK-06: Add network connectivity monitor with graceful degradation UI for payment method availability.
- ST-01-TK-07: Write end-to-end tests simulating WAN failure during transaction flow with reconnect scenarios.

---

### Epic 2: Mobile App Search and Inventory Experience

Improve mobile app search accuracy and performance by integrating real-time local inventory data, ensuring customers see relevant in-stock items and reducing fulfillment failures from out-of-stock selections.

#### 2.1 Filter search results by real-time local inventory availability

**Priority:** High   |   **Tags:** `mobile-app` `inventory` `performance`

> Mobile app search surfaces out-of-stock items without considering local store inventory, causing 20% inaccurate in-stock badges, failed fulfillment, and NPS of -30 for affected customers. Solution must integrate real-time inventory feeds into search ranking while respecting 800ms p95 latency budget and avoiding undisclosed price personalization.

**User story**
- As a mobile app user, I want search results to prioritize items actually in stock at my selected store, so that I don't waste time adding unavailable items to my cart only to receive a fulfillment failure email.

**Acceptance criteria**
- Given a customer has selected a preferred store, when they search for a product category (e.g. 'diapers size 4'), then results are ranked with in-stock items appearing before out-of-stock items.
- Given a product is out of stock at the customer's selected store, when it appears in search results, then the in-stock badge displays 'Out of stock at [Store Name]' with accuracy ≥95%.
- Given a customer views an out-of-stock item, when the item detail page loads, then substitute suggestions appear in the initial viewport (not requiring scroll) with in-stock alternatives.
- Given the inventory-aware search feature is enabled, when search requests are processed, then p95 latency remains under 800ms measured at the client.
- Given inventory state influences result ranking, when a customer views search results, then a disclosure statement appears (e.g. 'Results prioritized by availability at your store') to satisfy legal requirements for inventory-based personalization.

**Tasks**
- ST-02-TK-01: Build inventory service API endpoint returning store-level stock status with <100ms p95 response time.
- ST-02-TK-02: Integrate inventory data into search indexing pipeline with real-time update streaming from store systems.
- ST-02-TK-03: Modify search ranking algorithm to boost in-stock SKUs while maintaining relevance scoring.
- ST-02-TK-04: Add client-side inventory badge component with out-of-stock messaging and store name display.
- ST-02-TK-05: Implement substitute product recommendation API using in-stock alternatives from same category.
- ST-02-TK-06: Add personalization disclosure footer component with configurable legal messaging.
- ST-02-TK-07: Run load tests validating 800ms p95 latency under peak traffic with inventory enrichment enabled.

---

### Epic 3: Pharmacy Omnichannel Integration

Unify pharmacy refill request handling across mobile app and IVR channels into a single system of record, eliminating lost refill requests and ensuring HIPAA-compliant notification routing.

#### 3.1 Unify pharmacy refill channels to single Rx Hub backend

**Priority:** High   |   **Tags:** `pharmacy` `mobile-app` `compliance`

> Mobile app and IVR refill systems write to separate backends without real-time reconciliation, causing the top pharmacy helpline complaint where customers believe they've submitted a refill request but pharmacy has no record. Both channels must write to Rx Hub as system of record with HIPAA-compliant notifications requiring opt-in, verified contact routing, and 7-year audit retention.

**User story**
- As a pharmacy customer, I want my refill request to appear immediately in the pharmacy system regardless of whether I use the mobile app or phone IVR, so that my prescription is ready when promised and I don't experience the frustration of 'lost' refill requests.

**Acceptance criteria**
- Given a customer submits a refill via mobile app, when the request is confirmed, then it writes to Rx Hub and appears in the pharmacy fulfillment queue within 30 seconds.
- Given a customer submits a refill via IVR, when the request is confirmed, then it writes to Rx Hub with identical data schema as mobile app submissions.
- Given a refill request is written to Rx Hub, when the pharmacy marks it ready for pickup, then notifications are sent only to the patient's verified contact on file (not household default) with opt-in stored on the prescription record.
- Given a refill notification is sent, when the transaction completes, then an audit log entry is created and retained for 7 years including patient ID, contact method, timestamp, and opt-in status.
- Given both channels are live on Rx Hub, when a customer checks refill status in either channel, then the displayed status matches the pharmacy fulfillment system with ≤1 minute lag.

**Tasks**
- ST-03-TK-01: Design unified refill request schema with required fields for both mobile app and IVR submission paths.
- ST-03-TK-02: Migrate mobile app refill submission to write directly to Rx Hub API with idempotency keys.
- ST-03-TK-03: Migrate IVR refill submission to write to Rx Hub API using identical schema as mobile app.
- ST-03-TK-04: Build notification service routing messages to verified patient contact with opt-in validation.
- ST-03-TK-05: Implement HIPAA-compliant audit logging with 7-year retention and tamper-proof storage.
- ST-03-TK-06: Add real-time status sync between Rx Hub and pharmacy fulfillment system with change data capture.
- ST-03-TK-07: Write integration tests validating end-to-end refill flow consistency across both channels.

---

### Epic 4: Loyalty Program Transparency

Provide customers with clear visibility into loyalty tier status, progress tracking, and downgrade warnings to reduce confusion and support inquiries around tier changes.

#### 4.1 Add loyalty tier progress visibility and downgrade warnings

**Priority:** Medium   |   **Tags:** `loyalty` `mobile-app`

> Customers do not understand how tier status is earned or lost, leading to confusion and frustration when status changes occur without clear explanation. Solution must provide real-time progress tracking and proactive downgrade warnings. Source material does not specify the tier calculation rules themselves, so implementation requires product owner input on business logic.

**User story**
- As a loyalty program member, I want to see how close I am to earning or losing my tier status with clear explanations of the rules, so that I understand what actions earn points and can avoid unexpected downgrades.

**Acceptance criteria**
- Given a customer views their loyalty profile in the mobile app, when the page loads, then a progress bar displays their current position toward the next tier or days remaining until downgrade evaluation.
- Given a customer is within 30 days of a tier downgrade evaluation, when they open the mobile app, then a banner notification explains the downgrade risk and actions needed to maintain status.
- Given a customer earns or loses tier status, when the change occurs, then an email and push notification explains the specific criteria that triggered the change with a link to full tier rules.
- Given a customer taps 'How do I earn status?', when the help content loads, then it displays tier-specific earning rules in plain language with examples (e.g. '$500 spent in 90 days = Gold').
- Given tier status changes are communicated, when the message is sent, then it includes the effective date and any grace period before benefits change.

**Tasks**
- ST-04-TK-01: Conduct discovery spike with product owner to document tier calculation business rules and thresholds.
- ST-04-TK-02: Build loyalty tier calculation API exposing current status, progress percentage, and days to evaluation.
- ST-04-TK-03: Add loyalty profile UI component with progress bar and tier status visualization in mobile app.
- ST-04-TK-04: Implement downgrade warning notification service triggering 30 days before evaluation date.
- ST-04-TK-05: Create tier change notification templates for email and push with dynamic criteria explanation.
- ST-04-TK-06: Build in-app help content CMS integration displaying tier-specific rules with examples.

---

### Epic 5: Store Associate Platform Standards

Establish and enforce hardware compatibility requirements for store associate tooling, ensuring new features work on legacy handheld devices while planning for future hardware refresh.

#### 5.1 Document Android 7 compatibility requirements for store associate tools

**Priority:** Medium   |   **Tags:** `store-associate` `mobile-app` `platform`

> Store handheld inventory scanners run Android 7 without security patch support. Replacement hardware is approved for FY26, but any new store-side tooling built in Q3 must account for this legacy floor. This is a documentation and architecture guardrail story, not a feature delivery.

**User story**
- As a platform engineer building store associate tools, I want clear Android 7 compatibility requirements and fallback patterns documented, so that I don't ship features that fail on the current handheld fleet.

**Acceptance criteria**
- Given a new mobile-app story targets store associate use cases, when the story is created, then it includes an explicit hardware floor declaration (Android version) in acceptance criteria.
- Given the engineering architecture wiki exists, when store-facing mobile development standards are documented, then Android 7 is listed as the minimum supported version through Q2 FY26 with specific unsupported APIs enumerated (e.g. notification channels, adaptive icons).
- Given a pull request introduces a feature using Android APIs newer than API level 24 (Android 7), when CI runs, then automated checks flag the incompatibility and require explicit fallback code or justification.
- Given the FY26 hardware replacement timeline, when the Android 7 floor constraint is documented, then it includes a sunset date (Q2 FY26) and escalation path for emergency exceptions.

**Tasks**
- ST-05-TK-01: Document Android 7 compatibility requirements in engineering wiki with unsupported API list and fallback patterns.
- ST-05-TK-02: Create story template for store associate features requiring hardware floor declaration in AC.
- ST-05-TK-03: Add CI lint rule detecting Android API level >24 usage without compatibility annotations.
- ST-05-TK-04: Build automated test suite running store associate app flows on Android 7 emulator.
- ST-05-TK-05: Add sunset date tracking for Android 7 floor with Q2 FY26 milestone and stakeholder notification.

---

## 🔍 Gaps detected

Capabilities implied by the source material that are not represented in the existing backlog.

- **Offline transaction reconciliation after WAN recovery** — ST-01 and #1156 enable offline cash transactions during WAN outages but neither story addresses how these transactions sync back to central systems once connectivity is restored, which is critical for inventory accuracy, financial reporting, and fraud detection.
  - *Evidence:* ST-01 describes local SKU pricing validation and offline transaction capability but does not specify reconciliation mechanism; #1156 mentions SQLite cache refresh but no mention of bidirectional sync after outage resolution.
- **Real-time inventory feed reliability and fallback behavior** — ST-02 proposes integrating real-time inventory feeds into search ranking, but neither the new story nor existing tickets address what happens when the inventory feed is stale, unavailable, or contradicts local store systems, which would directly impact customer experience.
  - *Evidence:* ST-02 mentions '20% inaccurate in-stock badges' as the problem but doesn't specify how the solution handles feed latency, timeouts, or discrepancies; #1089 mentions switching from polling to event streams but doesn't address reliability.
- **Pharmacy refill notification delivery failure handling** — ST-03 specifies HIPAA-compliant notification requirements (opt-in, verified contact, audit retention) but neither the story nor #1198 address what happens when SMS/email delivery fails or when a patient's verified contact is outdated, which could block critical medication access.
  - *Evidence:* ST-03 lists notification requirements but contains no mention of retry logic, delivery confirmation, or fallback channels; #1198 focuses on system-of-record unification but not notification reliability.
- **Loyalty tier calculation business logic definition** — ST-04 explicitly flags that 'source material does not specify the tier calculation rules themselves' and requires product owner input, but there is no story to document or implement those rules once defined, creating a dependency blocker.
  - *Evidence:* ST-04 description states 'implementation requires product owner input on business logic' but no corresponding story exists to capture that logic or build the calculation engine that the progress UI would depend on.

## ⚠️ Conflicts

New stories that contradict architectural constraints or in-flight work.

- **Story ST-02** conflicts with **C-05** (severity: medium)
  - Story proposes integrating inventory into search ranking, which could be interpreted as price personalization based on inventory state; story description says it respects the no-undisclosed-personalization constraint but doesn't specify disclosure mechanism.

## ♻️ Possible duplicates

New stories that overlap with existing JIRA / GitHub tickets.

- **Story ST-01** overlaps with **#1156** (confidence: high)
  - Both describe enabling offline cash sales and returns under $50 during WAN outages at POS, including local SKU/pricing cache and the same card-payment-online-only requirement.
- **Story ST-02** overlaps with **#1247** (confidence: high)
  - Both address the same problem of search surfacing out-of-stock items without considering local store inventory, causing customer dissatisfaction and failed fulfillment.
- **Story ST-03** overlaps with **#1198** (confidence: high)
  - Both describe unifying dual pharmacy refill paths (mobile app and IVR) to write to Rx Hub as single system of record to eliminate race conditions and customer confusion.
- **Story ST-04** overlaps with **#1102** (confidence: high)
  - Both address lack of loyalty tier progress visibility and propose in-app views showing points, thresholds, and tier evaluation timing to reduce downgrade confusion.
