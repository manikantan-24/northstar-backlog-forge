# Backlog Synthesis

*Synthesized from: meeting_notes.txt*

## Summary

Q3 planning meeting synthesized customer feedback across five key areas: POS offline capability during network outages, mobile app search surfacing out-of-stock items, pharmacy refill system fragmentation, loyalty tier status clarity, and legacy store hardware constraints. Team assigned owners and flagged compliance considerations for PCI, advertising rules, and HIPAA.

## Epics (5)

### Epic 1: POS Offline Resilience

Enable point-of-sale systems to continue serving customers during WAN outages by supporting cash-only transactions using locally cached catalog data, ensuring revenue continuity during network disruptions.

#### 1.1 Enable offline cash-only POS transactions during WAN outages

**Priority:** High   |   **Tags:** `pos` `offline-mode` `payments` `performance`

> POS systems currently go fully offline during WAN outages, preventing even cash transactions because SKU pricing cannot be validated locally. Store managers are forced to turn customers away. This story addresses enabling cash-only sales using the existing SQLite catalog cache that refreshes hourly when online. Card sales remain forbidden per PCI requirements (C-15). This partially addresses the offline capability gap but does not solve the broader card-payment offline scenario.

**User story**
- As a store cashier, I want to process cash sales when the WAN is down, so that I can continue serving customers without turning them away during network outages.

**Acceptance criteria**
- Given the POS lane has lost WAN connectivity, when a cashier scans a SKU that exists in the local SQLite cache, then the POS displays the cached price and allows the transaction to proceed for cash payment only.
- Given the POS lane is offline, when a cashier attempts a card payment, then the POS blocks the transaction and displays a message indicating cash-only mode during network outage.
- Given the POS lane has been offline for more than one hour (exceeding cache refresh interval), when a cashier scans a SKU, then the POS displays a warning that pricing may be stale but still allows the cash transaction to proceed.
- Given the POS lane reconnects to the WAN, when the next transaction occurs, then offline transactions are synced to the central system within 30 seconds and the POS resumes normal card-accepting operation.
- Given the POS processes 100 offline cash transactions during a 40-minute outage, when connectivity is restored, then all transactions reconcile successfully with no data loss and transaction latency returns to <250ms p95 (C-02).

**Tasks**
- ST-01-TK-01: Implement network connectivity detection in POS client to trigger offline mode when WAN is unavailable.
- ST-01-TK-02: Add SQLite cache query logic to retrieve SKU pricing when POS is in offline mode.
- ST-01-TK-03: Modify payment method selection UI to disable card options and display cash-only message during offline mode.
- ST-01-TK-04: Build transaction queue in POS client to store offline cash transactions for sync when connectivity returns.
- ST-01-TK-05: Implement transaction sync endpoint to reconcile offline transactions with central system within 30 seconds of reconnection.
- ST-01-TK-06: Add stale pricing warning UI when cache age exceeds 60 minutes during offline operation.
- ST-01-TK-07: Create load test scenario simulating 100 offline transactions followed by reconnection to verify reconciliation and latency recovery.

---

### Epic 2: Mobile App Inventory Integration

Improve mobile app search and product discovery by integrating real-time local inventory signals, ensuring customers see accurate stock availability and prioritize in-stock items in search results.

#### 2.1 Integrate local inventory availability into mobile app search ranking

**Priority:** High   |   **Tags:** `mobile-app` `search` `inventory` `performance`

> Mobile app search currently surfaces out-of-stock items as top results because ranking does not factor local inventory. Customers add items to cart, check out, and receive fulfillment failure emails. Stock badges are also inaccurate 20% of the time. This story focuses on integrating real-time inventory signals into the search ranking algorithm to deprioritize locally unavailable items. Performance constraint C-03 requires search latency p95 to remain under 800ms. Constraint C-20 forbids polling at intervals shorter than 60 seconds, so implementation must use the inventory event stream. Improving badge accuracy is a separate concern and may warrant its own story if root cause differs from ranking logic.

**User story**
- As a mobile app customer, I want search results to show in-stock items at my local store first, so that I don't waste time adding unavailable items to my cart only to receive a fulfillment failure email.

**Acceptance criteria**
- Given a customer has selected a preferred store in the mobile app, when they search for 'diapers size 4', then items in stock at that store appear ranked higher than out-of-stock items, with equal relevance scores.
- Given a customer searches for a product category with mixed inventory, when the search results render, then each item displays an accurate stock badge (In Stock / Low Stock / Out of Stock) based on inventory data no older than 60 seconds.
- Given the inventory event stream delivers a stock-out event for a SKU, when a customer searches within 60 seconds of that event, then that SKU is demoted in ranking or marked out-of-stock in the badge.
- Given 10,000 concurrent mobile app users perform searches with inventory filtering enabled, when measuring p95 search latency, then latency remains under 800ms (C-03).
- Given a SKU transitions from out-of-stock to in-stock, when customers search for that SKU within 90 seconds, then it appears in top results with an accurate 'In Stock' badge.

**Tasks**
- ST-02-TK-01: Subscribe mobile app search service to inventory event stream to receive real-time stock updates.
- ST-02-TK-02: Build in-memory cache for store-level inventory status with 60-second TTL to comply with C-20 polling constraint.
- ST-02-TK-03: Modify search ranking algorithm to boost in-stock items and demote out-of-stock items based on preferred store inventory.
- ST-02-TK-04: Update search API response schema to include stock badge data (In Stock / Low Stock / Out of Stock) for each result.
- ST-02-TK-05: Modify mobile app search results UI to display stock badges alongside product listings.
- ST-02-TK-06: Run load test with 10,000 concurrent users to verify p95 search latency remains under 800ms with inventory filtering enabled.

---

### Epic 3: Pharmacy System Integration

Consolidate pharmacy refill workflows through Rx Hub as the single system of record, eliminating fragmentation across mobile app and IVR channels to ensure reliable prescription fulfillment.

#### 3.1 Route all pharmacy refill requests through Rx Hub service to eliminate system fragmentation

**Priority:** High   |   **Tags:** `pharmacy` `mobile-app` `integration` `compliance`

> Customers refilling prescriptions via the mobile app or IVR phone line encounter conflicts because the two channels write to different backend systems that do not reconcile in real time. Pharmacies report having no record of app-initiated refills. Constraint C-06 mandates that all prescription operations must go through Rx Hub as the single system of record. This story addresses routing both app and IVR flows through Rx Hub to eliminate the split-brain scenario. Compliance constraints C-08, C-09, and C-10 apply to any customer-facing medication features: explicit opt-in, verified contact delivery, and 7-year audit retention.

**User story**
- As a pharmacy customer, I want my refill request to reach the pharmacy regardless of whether I use the mobile app or call the IVR line, so that I can pick up my medication without confusion or delay.

**Acceptance criteria**
- Given a customer initiates a refill via the mobile app, when the request is submitted, then it writes to Rx Hub only (C-06) and is immediately visible to pharmacy staff systems with no manual reconciliation required.
- Given a customer initiates a refill via the IVR phone line, when the request is submitted, then it writes to Rx Hub only (C-06) and is immediately visible to both pharmacy staff systems and the mobile app refill history.
- Given a customer has opted in to medication notifications (C-08), when a refill is ready, then the notification is sent only to the patient's verified contact method (C-09) and logged in the audit trail with 7-year retention (C-10).
- Given a customer submits a refill via the mobile app at 10:00 AM and calls the pharmacy at 10:05 AM, when the pharmacist checks the system, then the refill request submitted 5 minutes earlier is visible with correct timestamp and medication details.
- Given 1,000 concurrent refill requests across app and IVR, when all requests route through Rx Hub, then zero split-brain incidents occur (measured as customer complaints of 'no record' at pickup).

**Tasks**
- ST-03-TK-01: Refactor mobile app refill submission to call Rx Hub API instead of legacy pharmacy backend.
- ST-03-TK-02: Refactor IVR refill submission to call Rx Hub API instead of legacy pharmacy backend.
- ST-03-TK-03: Implement audit logging in Rx Hub for all refill requests with 7-year retention per C-10.
- ST-03-TK-04: Add opt-in verification check to Rx Hub notification flow per C-08 before sending medication notifications.
- ST-03-TK-05: Update pharmacy staff system integration to read refill requests exclusively from Rx Hub.
- ST-03-TK-06: Verify notification delivery only to verified contact methods per C-09 in Rx Hub notification service.
- ST-03-TK-07: Create end-to-end test simulating concurrent app and IVR refills to verify zero split-brain incidents.

---

### Epic 4: Loyalty Program Transparency

Provide customers with clear visibility into loyalty tier qualification rules, progress tracking, and tier change history to reduce confusion and improve trust in the loyalty program.

#### 4.1 Surface loyalty tier rules and status timeline in customer-facing channels

**Priority:** Medium   |   **Tags:** `loyalty` `mobile-app` `ecommerce` `transparency`

> Customers do not understand how loyalty tier status is earned or lost, leading to confusion and frustration when downgraded without clear visibility into timing or qualification rules. They receive upgrade/downgrade emails but lack transparency into the underlying logic. This story focuses on surfacing tier qualification rules, progress indicators, and a timeline of tier changes in the mobile app and web account section. Constraint C-23 forbids direct database access to the loyalty system; all reads must go through the Loyalty API. If tier logic involves personalized pricing or segment-based offers, constraint C-12 requires Legal review before shipping.

**User story**
- As a loyalty program member, I want to see how I earn and lose tier status and view my tier history, so that I understand my current standing and am not surprised by downgrades.

**Acceptance criteria**
- Given a customer is logged into the mobile app or web account page, when they navigate to the loyalty section, then they see their current tier, the criteria for maintaining it (e.g., '$500 spend in trailing 90 days'), and their progress toward the next tier.
- Given a customer's tier status changes (upgrade or downgrade), when they view their loyalty section within 24 hours of the change, then they see a timeline entry showing the date, previous tier, new tier, and reason for change.
- Given a customer is approaching tier downgrade (e.g., 30 days before expiration), when they open the mobile app, then they receive a proactive in-app message warning them and showing exactly what spend or activity would preserve their tier.
- Given the loyalty tier display requires reading tier rules and customer history, when the mobile app or web page fetches this data, then all reads go through the Loyalty API (C-23) with no direct database access.
- Given a customer views their loyalty tier page on 3G connectivity, when the page loads, then p95 load time is under 1.5 seconds (C-01).

**Tasks**
- ST-04-TK-01: Extend Loyalty API to expose tier qualification rules and customer progress toward next tier.
- ST-04-TK-02: Extend Loyalty API to expose tier change timeline with date, previous tier, new tier, and reason.
- ST-04-TK-03: Build mobile app loyalty section UI to display current tier, qualification criteria, and progress indicators.
- ST-04-TK-04: Build web account page loyalty section UI to display current tier, qualification criteria, and progress indicators.
- ST-04-TK-05: Implement proactive in-app notification trigger for customers approaching tier downgrade within 30 days.
- ST-04-TK-06: Optimize Loyalty API response caching to achieve p95 page load under 1.5 seconds on 3G per C-01.

---

### Epic 5: Store Associate Platform Constraints

Document hardware and OS constraints for Android 7 handheld devices used by store associates to guide roadmap planning and prevent investment in unsupported capabilities until FY26 hardware refresh.

#### 5.1 Document Android 7 hardware constraints for store-associate tooling roadmap

**Priority:** Low   |   **Tags:** `inventory` `store-associate` `platform` `documentation`

> Store associate handheld scanners run Android 7 with no security patch support, creating constraints for new tooling. Constraints C-16, C-17, and C-18 document these limitations: no BLE central role, limited Camera2 API features, and background services capped at 60 seconds. These devices are in maintenance mode until the FY26 hardware refresh. This is not a user story but an engineering planning task: catalog the constraint surface, document workarounds, and ensure roadmap items for store-associate tools respect these limits. If the Parser Agent had flagged this as a planning observation rather than a story-worthy topic, it might have been handled differently. Given the input, this is better represented as backlog grooming or a spike rather than a user story.

**User story**
- As a platform engineer supporting store-associate tools, I want documented constraints for Android 7 handheld hardware, so that new tooling features do not assume capabilities unavailable until the FY26 refresh.

**Acceptance criteria**
- Given a new store-associate tool feature is proposed, when the engineering team reviews it, then they reference a wiki page or ADR listing Android 7 constraints (C-16, C-17, C-18) with workaround patterns for each.
- Given a tool feature requires BLE central role, when the requirement is identified, then the feature is scoped to use BLE peripheral role only or deferred until FY26 hardware refresh.
- Given a tool feature requires background processing beyond 60 seconds, when the requirement is identified, then the feature is redesigned to use foreground service patterns or deferred until FY26 hardware refresh.
- Given a tool feature requires Camera2 API capabilities beyond preview and still capture, when the requirement is identified, then the feature is rescoped to use only supported Camera2 features or deferred until FY26 hardware refresh.

**Tasks**
- ST-05-TK-01: Research and catalog all Android 7 OS and hardware limitations affecting BLE, Camera2, and background services per C-16, C-17, C-18.
- ST-05-TK-02: Create wiki page or ADR documenting Android 7 constraints with workaround patterns for BLE, camera, and background processing.
- ST-05-TK-03: Review existing store-associate tool roadmap items to flag features incompatible with Android 7 constraints.
- ST-05-TK-04: Add constraint checklist to feature proposal template for store-associate tools to prevent Android 7 incompatibilities.

---

## 🔍 Gaps detected

Capabilities implied by the source material that are not represented in the existing backlog.

- **Offline card payment strategy beyond WAN outages** — ST-01 explicitly notes that card sales remain unsolved during WAN outages due to PCI online auth requirements (C-15). No story or existing ticket addresses store-and-forward, alternate connectivity, or fallback authorization patterns for card transactions when primary network fails.
  - *Evidence:* ST-01 states 'Card sales remain forbidden per PCI requirements (C-15). This partially addresses the offline capability gap but does not solve the broader card-payment offline scenario.' No existing tickets in the candidate sets address this.
- **Root cause and remediation for search result stock badge inaccuracy** — ST-02 mentions that stock badges are inaccurate 20% of the time and notes this is a separate concern from ranking. Neither the new stories nor existing tickets (NS-412 addresses badge presence, not accuracy) tackle why badges are wrong or how to fix the data quality issue.
  - *Evidence:* ST-02 description: 'Stock badges are also inaccurate 20% of the time... Improving badge accuracy is a separate concern and may warrant its own story if root cause differs from ranking logic.'
- **IVR phone line integration with Rx Hub** — ST-03 states that IVR phone line refill requests write to a different backend than the mobile app, causing pharmacy conflicts. The story covers routing both channels through Rx Hub but does not detail the IVR integration work itself, which is distinct from the app flow.
  - *Evidence:* ST-03: 'Customers refilling prescriptions via the mobile app or IVR phone line encounter conflicts because the two channels write to different backend systems.' No existing ticket addresses IVR-to-Rx-Hub integration.
- **Loyalty tier qualification and downgrade business logic transparency** — ST-04 surfaces tier rules to customers but does not address whether the underlying tier calculation logic is documented, testable, or observable for internal teams. If customers are confused, operators likely lack tooling to diagnose or explain tier changes in support scenarios.
  - *Evidence:* ST-04 describes customer confusion about tier downgrades and lack of transparency. NS-389 improves the email but neither story nor existing tickets address internal visibility, operator tooling, or tier calculation auditability.

## ⚠️ Conflicts

New stories that contradict architectural constraints or in-flight work.

- **Story ST-01** conflicts with **C-13** (severity: low)
  - C-13 already mandates cash sales during WAN outages using SQLite cache; ST-01 describes enabling this capability as new work, suggesting either the constraint is not enforced or ST-01 misunderstands current state.

## ♻️ Possible duplicates

New stories that overlap with existing JIRA / GitHub tickets.

- **Story ST-02** overlaps with **NS-412** (confidence: high)
  - Both address showing local store inventory availability in customer-facing search/results; NS-412 is badge-focused while ST-02 targets ranking, but the underlying integration work (pulling local inventory into the result stream) is identical.
- **Story ST-04** overlaps with **NS-389** (confidence: medium)
  - NS-389 focuses on improving the tier downgrade email content, while ST-04 is broader (in-app timeline, rules, progress); the email improvement is a subset of ST-04's scope and would likely be absorbed.
