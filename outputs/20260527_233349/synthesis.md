# Backlog Synthesis

*Synthesized from: meeting_notes.txt*

## Summary

Q3 planning meeting focused on customer-facing problems across POS, mobile app, pharmacy, and loyalty systems. Five distinct themes emerged: POS offline capability during internet outages, mobile app search surfacing out-of-stock items, pharmacy refill system fragmentation, loyalty tier status clarity, and hardware constraints for store-associate tools. Team assigned owners and flagged compliance considerations for PCI and HIPAA.

## Epics (5)

### Epic 1: POS Offline Resilience & Payment Continuity

Enable POS systems to continue critical cash sales operations during WAN outages, preventing revenue loss while maintaining PCI compliance for card transactions. Includes local caching, offline transaction queueing, and reconciliation workflows.

#### 1.1 Enable offline cash sales at POS lanes when WAN connectivity is lost

**Priority:** High   |   **Tags:** `pos` `offline-mode` `payments` `performance`

> POS systems currently become completely non-functional when WAN connectivity drops, preventing all transactions including cash sales. This causes revenue loss and forces stores to turn customers away. The system must allow cashiers to process cash sales using local SKU catalog cache even when offline. Note: Card sales remain forbidden per PCI requirements (C-13), and returns over $50 require online authorization (C-12).

**User story**
- As a cashier, I want to process cash sales when the WAN is down, so that customers can complete purchases and the store doesn't lose revenue during connectivity outages.

**Acceptance criteria**
- Given the POS lane has lost WAN connectivity, when a cashier scans a SKU and selects cash payment for a transaction under $1000, then the sale completes successfully using the local SQLite cache.
- Given the POS lane is offline, when a cashier attempts a card payment, then the system displays a clear message that card payments require connectivity and suggests cash payment.
- Given the POS lane is offline, when a cashier attempts a return under $50, then the return processes successfully and queues for reconciliation when connectivity returns.
- Given the POS lane is offline, when a cashier attempts a return over $50, then the system blocks the transaction and displays a message that manager approval requires connectivity.
- Given the POS lane returns online after processing offline cash sales, when the reconciliation process runs, then all offline transactions sync to the central system within 5 minutes with p95 < 2 minutes.

**Tasks**
- ST-01-TK-01: Implement WAN connectivity detection and state management in POS client
- ST-01-TK-02: Create SQLite schema and sync process for local SKU catalog cache on POS terminals
- ST-01-TK-03: Build offline transaction queue with deduplication for cash sales and sub-$50 returns
- ST-01-TK-04: Add payment method validation logic to block card transactions when offline
- ST-01-TK-05: Develop reconciliation service to sync queued offline transactions when connectivity restored
- ST-01-TK-06: Update POS UI to display offline mode status and payment method restrictions
- ST-01-TK-07: Create end-to-end test suite for offline cash sales and reconnection reconciliation scenarios

---

### Epic 2: Mobile App Inventory Accuracy & Search Experience

Improve mobile app search and product discovery by integrating real-time inventory data to prevent customers from attempting to purchase out-of-stock items, reducing failed orders and customer frustration.

#### 2.1 Surface real-time local inventory status in mobile app search results

**Priority:** High   |   **Tags:** `mobile-app` `inventory` `search` `performance`

> Mobile app search currently ranks results without considering local store inventory, leading to prominent display of out-of-stock items. Customers add unavailable items to cart, complete checkout, and receive failure notifications afterward, resulting in NPS of -30 for affected customers. Search must factor real-time inventory and display accurate stock badges. In-stock badges are currently inaccurate 20% of the time. Solution must comply with C-03 (800ms p95 search latency) and C-17 (no polling under 60s; use inventory event stream).

**User story**
- As a mobile app user, I want search results to show which items are in stock at my local store, so that I don't waste time adding unavailable items to my cart and placing orders that will fail.

**Acceptance criteria**
- Given a customer has selected a local store, when they search for a product, then search results include an accurate in-stock badge reflecting inventory status from the last 60 seconds via the inventory event stream.
- Given a customer searches for a product category, when results are displayed, then in-stock items at the selected store rank higher than out-of-stock items, all other ranking factors being equal.
- Given search results include out-of-stock items, when a customer views the results, then out-of-stock items display a clear 'Out of Stock' badge and suggest available alternatives if substitutes exist within 2 categories.
- Given the mobile app search executes with inventory filtering, when measured under synthetic 3G conditions, then p95 latency remains under 800ms.
- Given in-stock badge accuracy is currently 80%, when this feature ships, then accuracy improves to at least 95% as measured by spot-checks against actual store inventory.

**Tasks**
- ST-02-TK-01: Integrate mobile app backend with inventory event stream to consume real-time stock updates
- ST-02-TK-02: Build in-memory cache layer for store-level inventory with 60-second staleness tolerance
- ST-02-TK-03: Modify search ranking algorithm to boost in-stock items for selected store location
- ST-02-TK-04: Add inventory status badges and out-of-stock messaging to mobile app search results UI
- ST-02-TK-05: Implement substitute product recommendation logic for out-of-stock items within 2 categories
- ST-02-TK-06: Conduct performance testing under synthetic 3G to validate p95 latency under 800ms
- ST-02-TK-07: Create spot-check validation framework to measure in-stock badge accuracy against actual inventory

---

### Epic 3: Pharmacy System Integration & Data Consistency

Consolidate prescription refill workflows across mobile and IVR channels to use RxCore as the single system of record, eliminating customer-facing data inconsistencies and pharmacy helpline complaints.

#### 3.1 Unify prescription refill flows to use RxCore as single system of record

**Priority:** High   |   **Tags:** `pharmacy` `mobile-app` `integration`

> Customers refilling prescriptions via mobile app versus phone IVR write to different systems that don't reconcile in real time, causing the most frequent pharmacy helpline complaint: app refills don't appear in pharmacy systems. Both flows must write to RxCore (C-06) as the single system of record. This requires either migrating the IVR flow to RxCore or ensuring real-time synchronization, though the source material doesn't specify which path is preferred.

**User story**
- As a pharmacy customer, I want my prescription refill to appear in the pharmacy system immediately regardless of whether I use the app or phone line, so that my medication is ready when the pharmacy confirms and I don't face confusion at pickup.

**Acceptance criteria**
- Given a customer submits a refill via the mobile app, when the request completes, then the refill writes to RxCore and is immediately visible to pharmacy staff systems within 5 seconds.
- Given a customer submits a refill via the phone IVR, when the request completes, then the refill writes to RxCore and is immediately visible to pharmacy staff systems within 5 seconds.
- Given a customer submits a refill via either channel, when they check refill status in the mobile app within 30 seconds, then the status reflects the single source of truth from RxCore.
- Given both flows now use RxCore, when measuring refill-related helpline complaints over 30 days post-launch, then 'pharmacy has no record' complaints decrease by at least 80% from baseline.

**Tasks**
- ST-03-TK-01: Conduct technical spike to evaluate IVR-to-RxCore migration versus real-time sync approaches
- ST-03-TK-02: Migrate mobile app refill submission endpoint to write directly to RxCore API
- ST-03-TK-03: Migrate or synchronize IVR refill flow to write to RxCore within 5-second SLA
- ST-03-TK-04: Update pharmacy staff systems to read refill queue exclusively from RxCore
- ST-03-TK-05: Modify mobile app refill status display to source data from RxCore API
- ST-03-TK-06: Establish monitoring and alerting for RxCore write latency and cross-channel consistency
- ST-03-TK-07: Track pharmacy helpline complaint metrics to validate 80% reduction target post-launch

---

### Epic 4: Loyalty Program Transparency & Customer Engagement

Provide customers with clear visibility into loyalty tier earning rules, current status, and timeline for tier changes to reduce surprise downgrades and improve program engagement.

#### 4.1 Surface loyalty tier earning and retention rules with timeline transparency

**Priority:** Medium   |   **Tags:** `loyalty` `mobile-app` `customer-experience`

> Customers don't understand how they earn or lose loyalty tier status, leading to frustration when unexpectedly downgraded. No customer-facing surface currently explains tier rules or shows timeline/progress toward tier changes. The solution must surface earning mechanics, current status with progress indicators, and advance warning of downgrades. Source material doesn't specify exact rules or timeline, so these must be obtained from the loyalty team.

**User story**
- As a loyalty program member, I want to understand how I earn and maintain my tier status with clear visibility into my progress and upcoming changes, so that I'm not surprised by downgrades and can adjust my behavior to maintain benefits.

**Acceptance criteria**
- Given a customer views their loyalty profile in the mobile app, when the profile loads, then it displays their current tier, points/spend toward next tier, and points/spend required to maintain current tier with date ranges.
- Given a customer is at risk of tier downgrade within 60 days, when they view their profile or loyalty section, then a clear warning displays the downgrade date and specific actions needed to prevent it.
- Given a customer tier changes (upgrade or downgrade), when the change occurs, then they receive a notification explaining why the change happened with reference to the specific rule triggered.
- Given the loyalty tier rules page is published, when a customer navigates to it from their profile, then all earning, retention, and downgrade rules are explained in plain language with examples.
- Given tier status is displayed in mobile app, when measuring against Loyalty API as source of truth, then displayed status matches API within 5 minutes of any tier change.

**Tasks**
- ST-04-TK-01: Gather tier earning, retention, and downgrade rules documentation from loyalty business team
- ST-04-TK-02: Extend Loyalty API to expose tier progress, timeline, and downgrade risk calculations
- ST-04-TK-03: Build mobile app loyalty profile screen with tier status, progress bars, and date ranges
- ST-04-TK-04: Implement downgrade warning notification logic triggered at 60-day threshold
- ST-04-TK-05: Create loyalty tier rules content page with plain-language explanations and examples
- ST-04-TK-06: Add push notification service integration for tier change events with rule references

---

### Epic 5: Store-Associate Platform Constraints & Development Guardrails

Document Android 7 hardware and OS limitations for store-associate tooling, establish capability detection patterns, and create development guidelines to prevent production failures before FY26 hardware refresh.

#### 5.1 Document Android 7 constraints for store-associate tooling and establish hardware capability gating

**Priority:** Medium   |   **Tags:** `store-associate` `pos` `platform` `documentation`

> Store-associate handhelds run Android 7 with no security patches, and hardware refresh is not scheduled until FY26. New tooling must avoid assuming capabilities unavailable on Android 7 per constraints C-14 and C-15. This story captures the need to document these limitations, establish capability detection patterns, and create guidelines for feature gating or deferral until refresh. This is primarily a technical constraint awareness task rather than a feature delivery.

**User story**
- As a developer building store-associate tooling, I want clear documentation and patterns for Android 7 hardware constraints, so that I don't build features that will fail in production or require expensive rework.

**Acceptance criteria**
- Given the engineering wiki, when a developer navigates to store-associate platform documentation, then Android 7 constraints (no BLE central, limited Camera2, 60s background service limit) are documented with code examples of capability detection.
- Given new store-associate tool stories are created, when they depend on functionality unavailable on Android 7, then the story explicitly calls out the constraint and either includes capability-gated implementation or marks as FY26-dependent.
- Given a shared utilities library for store-associate apps, when it includes hardware capability checks, then methods exist for detecting BLE central support, Camera2 advanced features, and safe background service patterns.
- Given a pre-flight checklist for store-associate deployments, when code review occurs, then reviewers explicitly verify Android 7 compatibility or confirm feature is appropriately gated/deferred.

**Tasks**
- ST-05-TK-01: Document Android 7 hardware constraints and limitations in engineering wiki with concrete examples
- ST-05-TK-02: Create shared Android utilities library with BLE, Camera2, and background service capability detection methods
- ST-05-TK-03: Develop story template for store-associate features that includes Android 7 compatibility checklist
- ST-05-TK-04: Establish code review pre-flight checklist for store-associate deployments with Android 7 verification
- ST-05-TK-05: Create example implementations of feature gating for capabilities unavailable on Android 7

---

## 🔍 Gaps detected

Capabilities implied by the source material that are not represented in the existing backlog.

- **Reconciliation workflow for offline POS transactions** — ST-01 enables offline cash sales, but neither new stories nor existing backlog address how offline transactions reconcile when connectivity returns, including conflict resolution, audit trails, and inventory synchronization.
  - *Evidence:* ST-01 describes offline cash sales using local cache, and C-11 mandates this capability, but no story covers the online/offline sync boundary or reconciliation failures.
- **Inventory accuracy improvement to reduce search result badge errors** — ST-02 notes that in-stock badges are inaccurate 20% of the time, but neither new stories nor existing backlog address the root cause of inventory data quality problems.
  - *Evidence:* ST-02 explicitly states 'In-stock badges are currently inaccurate 20% of the time' but only addresses surfacing the data, not improving its accuracy.
- **Customer notification system for tier status changes** — ST-04 requires advance warning of tier downgrades and progress indicators, but no story addresses the notification infrastructure or timing rules for when warnings are sent.
  - *Evidence:* ST-04 requires 'advance warning of downgrades' and NS-389 mentions downgrade emails already exist, but neither specifies the notification trigger logic or lead time.
- **IVR prescription refill migration or synchronization path** — ST-03 identifies that IVR and mobile app refill flows write to different systems, but doesn't specify whether IVR should migrate to RxCore or use real-time sync, leaving the implementation path undefined.
  - *Evidence:* ST-03 explicitly notes 'the source material doesn't specify which path is preferred' between migrating IVR to RxCore versus implementing synchronization.
- **Offline returns processing for sub-$50 transactions** — C-12 mandates that returns under $50 must be processable offline, but no story in the new or existing backlog addresses implementing this offline returns capability.
  - *Evidence:* C-12 states 'Returns under $50 MUST be processable offline' but no story covers offline returns processing, only offline sales (ST-01).

## ⚠️ Conflicts

New stories that contradict architectural constraints or in-flight work.

- **Story ST-01** conflicts with **C-11** (severity: low)
  - ST-01 requests offline cash sales capability that C-11 already mandates as a must-have requirement, suggesting the constraint may not be implemented rather than conflicting with it.

## ♻️ Possible duplicates

New stories that overlap with existing JIRA / GitHub tickets.

- **Story ST-02** overlaps with **NS-412** (confidence: high)
  - Both address surfacing local store inventory status in search results; NS-412 already covers the core work of showing store-specific stock badges.
- **Story ST-04** overlaps with **NS-389** (confidence: medium)
  - NS-389 addresses explaining tier downgrade context in emails, which is a subset of ST-04's broader goal to surface tier earning/retention rules and timelines.
