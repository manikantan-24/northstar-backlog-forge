# Backlog Synthesis

*Synthesized from: samples/meeting_notes.txt*

## Summary

The team reviewed Q3 customer-facing priorities based on recent feedback. Five main themes emerged: POS offline capabilities during internet outages, mobile app search surfacing out-of-stock items, pharmacy refill system fragmentation, loyalty tier visibility issues, and legacy hardware constraints for store associates. Self-checkout improvements, vendor portal redesign, and B2B membership were explicitly deferred.

## Epics (5)

### Epic 1: POS Offline Resilience

Enable point-of-sale systems to continue processing cash transactions during internet outages by leveraging local data caches, ensuring revenue continuity during connectivity disruptions while maintaining payment compliance constraints.

#### 1.1 Enable offline cash sales at POS during WAN outages

**Priority:** High   |   **Tags:** `pos` `offline-mode` `payments` `performance`

> POS systems currently cannot process any transactions when internet connectivity is lost, preventing even cash sales during outages. This causes revenue loss and customer frustration during peak hours. Implementation must use the existing local SQLite cache (refreshed hourly when online) per C-13. Card transactions remain forbidden offline per C-15 (PCI requirement).

**User story**
- As a store cashier, I want to ring up cash sales even when the internet is down, so that customers can complete purchases and the store does not lose revenue during outages.

**Acceptance criteria**
- Given the POS lane has lost WAN connectivity, when a cashier scans a SKU and selects cash payment, then the transaction completes successfully using the local SQLite cache.
- Given the POS lane is offline, when a cashier attempts a card payment, then the system displays an error message stating card payments require internet connectivity.
- Given the POS lane reconnects after being offline, when the system comes back online, then all offline cash transactions sync to the central system within 5 minutes.
- Given the local SQLite cache is older than 2 hours, when a cashier attempts an offline sale, then the system displays a warning that pricing may be stale but allows the transaction to proceed.
- Given a POS lane processes 50 offline cash transactions, when measured under load testing, then SKU lookup from SQLite cache maintains p95 latency under 250ms per C-02.

**Tasks**
- ST-01-TK-01: Implement network connectivity detection and offline mode toggle in POS application.
- ST-01-TK-02: Create transaction queue service to store offline cash sales locally and sync on reconnection.
- ST-01-TK-03: Add payment method validation to block card transactions when offline and display error UI.
- ST-01-TK-04: Implement cache staleness indicator to display warning when SQLite data exceeds 2-hour threshold.
- ST-01-TK-05: Build transaction reconciliation service to merge offline sales into central system within 5-minute SLA.
- ST-01-TK-06: Create load testing suite to validate p95 latency under 250ms for 50 concurrent offline transactions.

---

### Epic 2: Mobile App Inventory Intelligence

Integrate real-time inventory data into mobile app search and browsing experiences to prevent customers from attempting to purchase out-of-stock items, reducing fulfillment failures and improving NPS.

#### 2.1 Filter out-of-stock items from mobile app search results

**Priority:** High   |   **Tags:** `mobile-app` `inventory` `search` `performance`

> Mobile app search returns out-of-stock items as top results, leading to fulfillment failures and significant NPS damage (-30 vs +18 baseline). Implementation must respect 800ms p95 search latency constraint (C-03) and avoid polling inventory at intervals shorter than 60 seconds (C-19). The story assumes real-time inventory data is available via the inventory event stream.

**User story**
- As a mobile app customer, I want search results to show only in-stock items at my selected store, so that I do not waste time adding unavailable products to my cart and experiencing fulfillment failures.

**Acceptance criteria**
- Given a customer has selected a preferred store, when they search for a product, then out-of-stock items at that store do not appear in the top 10 results.
- Given inventory changes for a SKU, when the inventory event stream publishes the update, then the search index reflects the new stock status within 90 seconds.
- Given a customer searches for 'diapers size 4', when 3 results are in stock and 5 are out of stock, then only the 3 in-stock items appear in search results.
- Given mobile app search under realistic 3G network conditions, when measuring p95 latency with inventory filtering enabled, then latency remains under 800ms per C-03.
- Given a customer has no preferred store selected, when they search, then results include all items with a stock availability badge indicating nearest in-stock location.

**Tasks**
- ST-02-TK-01: Subscribe search indexing service to inventory event stream for real-time stock status updates.
- ST-02-TK-02: Extend search index schema to include per-store stock status and nearest-location metadata.
- ST-02-TK-03: Implement search query logic to filter out-of-stock SKUs based on customer's preferred store selection.
- ST-02-TK-04: Add stock availability badge UI component for search results when no preferred store is selected.
- ST-02-TK-05: Optimize search response payload and caching strategy to maintain p95 latency under 800ms on 3G.
- ST-02-TK-06: Create performance test suite to validate latency constraint under realistic network conditions.

---

### Epic 3: Pharmacy System Integration

Consolidate fragmented pharmacy refill request pathways into a single system of record (Rx Hub) to eliminate duplicate submissions, reduce customer complaints, and ensure compliance with architectural constraints.

#### 3.1 Consolidate pharmacy refill flows to single system of record

**Priority:** High   |   **Tags:** `pharmacy` `mobile-app` `integration` `compliance`

> Mobile app and IVR phone line write refill requests to separate backends without real-time reconciliation, causing the top complaint to pharmacy help line. Per C-06, all prescription operations must flow through Rx Hub. This story requires migrating both refill entry points to write to Rx Hub and decommissioning the duplicate backend. Medication-related notifications during this work must comply with C-08, C-09, and C-10.

**User story**
- As a pharmacy customer, I want my refill requests to be recorded consistently regardless of whether I use the mobile app or phone line, so that the pharmacy has an accurate record when I arrive to pick up my medication.

**Acceptance criteria**
- Given a customer submits a refill via mobile app, when the request is processed, then it writes to Rx Hub and appears in the pharmacy fulfillment queue within 30 seconds.
- Given a customer submits a refill via IVR, when the request is processed, then it writes to Rx Hub and appears in the pharmacy fulfillment queue within 30 seconds.
- Given a customer submits the same refill via both channels within 10 minutes, when the system detects the duplicate, then it consolidates to a single fulfillment request and notifies the customer.
- Given the legacy refill backend, when both channels are migrated to Rx Hub, then the legacy system is deprecated and all writes go only to Rx Hub per C-06.
- Given pharmacy staff query refills, when they search for a customer's prescription, then they see a unified view of all refill requests regardless of submission channel.

**Tasks**
- ST-03-TK-01: Migrate mobile app refill submission endpoint to write directly to Rx Hub API.
- ST-03-TK-02: Migrate IVR phone system refill submission to write directly to Rx Hub API.
- ST-03-TK-03: Implement duplicate detection logic in Rx Hub to consolidate refills submitted via multiple channels within 10-minute window.
- ST-03-TK-04: Build unified pharmacy staff view that aggregates refill requests from Rx Hub regardless of submission source.
- ST-03-TK-05: Deprecate legacy refill backend and update routing to ensure all writes flow only to Rx Hub per C-06.
- ST-03-TK-06: Validate medication-related notifications comply with C-08, C-09, and C-10 during migration.

---

### Epic 4: Loyalty Program Transparency

Surface loyalty tier rules, earning mechanics, and expiration timelines in customer-facing interfaces to reduce confusion around tier downgrades and improve program engagement.

#### 4.1 Surface loyalty tier earning and expiration rules in customer-facing UI

**Priority:** Medium   |   **Tags:** `loyalty` `mobile-app` `ecommerce`

> Customers do not understand how they earn or lose loyalty tier status, causing confusion when downgraded. The rules and timing are not visible on any customer-facing surface. Implementation must integrate via the Loyalty API per C-22 (direct database access forbidden). Story assumes tier calculation logic is already implemented in the loyalty system and only requires exposure in UI.

**User story**
- As a loyalty program member, I want to see how I earn and maintain my tier status including expiration dates, so that I understand when I might be downgraded and can take action to maintain my benefits.

**Acceptance criteria**
- Given a customer views their loyalty profile in the mobile app, when the screen loads, then it displays their current tier, points balance, points needed for next tier, and tier expiration date.
- Given a customer is within 30 days of tier expiration, when they view their profile, then a prominent notice explains they will be downgraded if they do not earn X additional points by the expiration date.
- Given a customer taps 'How do I earn points?', when the help screen loads, then it displays a clear breakdown of point-earning activities (e.g., $1 spent = 1 point, pharmacy refills = 50 points).
- Given the loyalty system via Loyalty API per C-22, when tier rules change, then customer-facing displays reflect the new rules within 24 hours without requiring app update.
- Given a customer receives a tier downgrade email, when they tap the link in the email, then they land on a page explaining why they were downgraded and how to re-earn the tier.

**Tasks**
- ST-04-TK-01: Create Loyalty API integration layer to fetch tier status, points balance, and expiration dates per C-22.
- ST-04-TK-02: Build loyalty profile screen UI to display current tier, points needed for next tier, and expiration timeline.
- ST-04-TK-03: Implement 30-day expiration warning banner with dynamic point gap calculation.
- ST-04-TK-04: Create point-earning rules help screen with configurable content pulled from Loyalty API.
- ST-04-TK-05: Build tier downgrade landing page linked from email notifications explaining downgrade reason and re-earn path.

---

### Epic 5: Store-Associate Platform Constraints

Document hardware and OS limitations of current store-associate tooling (Android 7 handheld scanners) to prevent engineering teams from designing incompatible features prior to FY26 hardware refresh.

#### 5.1 Document Android 7 hardware limitations for store-associate tooling

**Priority:** Medium   |   **Tags:** `store-associate` `pos` `platform`

> Store-side handheld scanners run Android 7 and cannot receive security patches; replacement approved for FY26. Per C-16 and C-17, new tooling must account for Android 7 limitations: no BLE central role (peripheral only), Camera2 API limited to preview + still capture, background services capped at 60 seconds. This is a documentation/guidance story, not a feature build. Any new store-associate feature stories must explicitly state Android 7 compatibility or wait for FY26 refresh.

**User story**
- As an engineer building store-associate tooling, I want clear documentation of Android 7 hardware constraints, so that I do not design features that cannot run on current devices and avoid rework or deployment blockers.

**Acceptance criteria**
- Given the engineering wiki, when an engineer navigates to the store-associate hardware section, then a page lists Android 7 limitations from C-16 including BLE, Camera2, and background service constraints.
- Given a new feature proposal for store-associate tools, when submitted for technical review, then the review checklist includes an Android 7 compatibility assessment per C-17.
- Given the FY26 hardware refresh timeline, when documented, then it includes the expected Android version upgrade and deprecated limitations that will be lifted post-refresh.
- Given an engineer attempts to use BLE central role in store-associate code, when the CI pipeline runs, then a linting rule flags the usage and links to the Android 7 constraints documentation.

**Tasks**
- ST-05-TK-01: Create engineering wiki page documenting Android 7 BLE, Camera2, and background service limitations per C-16.
- ST-05-TK-02: Add Android 7 compatibility assessment to store-associate feature review checklist per C-17.
- ST-05-TK-03: Document FY26 hardware refresh timeline and expected Android version upgrade on wiki page.
- ST-05-TK-04: Implement CI linting rule to flag BLE central role usage in store-associate codebase.

---

## 🔍 Gaps detected

Capabilities implied by the source material that are not represented in the existing backlog.

- **Reconciliation process for offline POS transactions** — ST-01 enables offline cash sales using local SQLite cache, but neither the new stories nor existing backlog address how offline transactions reconcile with central systems when connectivity resumes, including conflict resolution for inventory, returns, or pricing discrepancies.
  - *Evidence:* ST-01 references C-13's hourly cache refresh and offline cash sales capability, but no story covers the post-outage sync process that would be required for these transactions to flow into central reporting, inventory, and accounting systems.
- **Real-time inventory event stream integration for mobile app** — ST-02 assumes real-time inventory data is available via the inventory event stream and must respect C-19's prohibition on polling, but no story addresses how the mobile app consumes this event stream or handles the push notifications/updates required to keep search results current.
  - *Evidence:* ST-02 explicitly states 'The story assumes real-time inventory data is available via the inventory event stream' and references C-19, but neither new stories nor existing backlog (NS-412, NS-219, NS-419, NS-289) describe event stream subscription or client-side event handling.
- **IVR phone line migration to Rx Hub** — ST-03 requires migrating both mobile app and IVR refill entry points to Rx Hub, but no story addresses the IVR system integration work, including API contracts, error handling for voice flows, or decommissioning of the IVR's duplicate backend.
  - *Evidence:* ST-03 states 'Mobile app and IVR phone line write refill requests to separate backends' and requires 'migrating both refill entry points to write to Rx Hub,' but existing tickets (NS-176, NS-205) don't cover IVR-specific integration.
- **Pharmacy notification audit logging infrastructure** — ST-03 references C-08, C-09, and C-10 which require audit logs of medication notifications retained for 7 years, but no story addresses whether this logging infrastructure exists or needs to be built for the consolidated Rx Hub flows.
  - *Evidence:* C-10 mandates 'Audit log of every notification sent, retained 7 years' and ST-03 explicitly calls out compliance with C-08, C-09, and C-10, but NS-321 (the only audit log ticket) addresses HIPAA retention for general pharmacy logs, not notification-specific audit trails.
- **Android 7 hardware refresh migration plan** — ST-05 documents Android 7 limitations and notes replacement is approved for FY26, but no story addresses the migration plan for existing store-associate tooling that may currently violate C-16/C-17 constraints or how to handle the transition period.
  - *Evidence:* ST-05 states 'Store-side handheld scanners run Android 7 and cannot receive security patches; replacement approved for FY26' and C-17 requires stories to 'either gate by hardware capability or wait for the refresh,' but no backlog item covers inventory of affected tools or migration strategy.

## ⚠️ Conflicts

New stories that contradict architectural constraints or in-flight work.

- **Story ST-01** conflicts with **C-13** (severity: low)
  - ST-01 describes the capability that C-13 already mandates as 'must' — this is alignment, not conflict; however, if ST-01 implies net-new work when C-13 suggests the capability should already exist, there's a gap between current state and constraint.

## ♻️ Possible duplicates

New stories that overlap with existing JIRA / GitHub tickets.

- **Story ST-04** overlaps with **NS-389** (confidence: high)
  - Both address surfacing loyalty tier earning/downgrade rules to customers; NS-389 focuses on email context while ST-04 targets UI, but the underlying capability (exposing tier rules) is the same work.
- **Story ST-02** overlaps with **NS-412** (confidence: medium)
  - Both address inventory visibility in search results; NS-412 focuses on store-specific 'in stock' badges while ST-02 filters out-of-stock items entirely, but both solve the same customer problem of misleading availability.
