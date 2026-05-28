# NorthStar Retail — Engineering Architecture Constraints

**Owner:** Architecture Review Board
**Last reviewed:** March 2026
**Audience:** All product engineering teams. This page captures the constraints any new initiative must respect or formally exception out of.

---

## 1. Performance budgets

- **Mobile app cart-load p95** must stay under **1.5 seconds on a 3G connection**. We measure with synthetic transactions hourly. Regressions block release.
- **POS lane transaction latency** for a single SKU scan must stay under **250ms p95** including the network round trip. If the network is degraded, see Section 4.
- **Search query latency p95** in the mobile app and on the web must stay under **800ms**. The search service is rate-limited at the edge to protect this.

## 2. Required integrations

- All customer identity must flow through **NorthStar Identity (NSID)**. New auth flows MUST NOT introduce a separate customer credential store. Federation with NSID is the only supported pattern.
- All payment authorization MUST go through the **PaymentGateway** service. Direct calls to card processors are forbidden — this is a PCI scope requirement.
- All prescription-related operations MUST go through the **Rx Hub** service. Rx Hub is the only system of record for prescriptions; writes to other stores cause reconciliation failures.

## 3. Security and compliance

- **PCI scope reduction.** Card data must never touch our application servers. PaymentGateway hands back a tokenized reference; our code stores only the token.
- **HIPAA.** Any feature that surfaces a medication name (push notifications, emails, SMS, in-app text) requires:
  - Explicit patient opt-in stored on the prescription record
  - Delivery only to the patient's *verified* contact method, never the household account default
  - Audit log of every notification sent, retained 7 years
- **Sanctions screening.** Customer-facing payment flows must screen the customer name against the OFAC SDN list before any transaction over $10,000. This is handled by PaymentGateway; do not bypass it.
- **Advertising compliance.** Price personalization based on customer segment or inventory state requires disclosure and Legal review. Cannot ship without sign-off.

## 4. Offline tolerance (in-store hardware)

- **POS lanes** MUST continue to process **cash sales** even when the WAN is down. The local SQLite cache holds the SKU catalog and refreshes hourly when online.
- **Returns under $50** MUST be processable offline. Returns over $50 require manager approval and online authorization.
- **Card sales** when the WAN is down: FORBIDDEN. PCI requires online auth.
- **Store-associate handhelds** running Android 7 (legacy hardware in stores until the FY26 refresh) cannot use:
  - BLE central role (only peripheral supported)
  - Camera2 API features beyond preview + still capture
  - Background services that run more than 60 seconds

Stories that depend on functionality unavailable on Android 7 must explicitly say so and either gate by hardware capability or wait for the refresh.

## 5. Data residency

- Customer PII for US customers must remain in our US-East and US-West regions. Cross-region replication to APAC/EU is forbidden under the current privacy program.
- Analytics aggregates (no PII) may flow to our central data warehouse in US-East.

## 6. Forbidden patterns

- **Polling for inventory updates** from the mobile app at intervals shorter than 60 seconds. Use the inventory event stream instead.
- **Synchronous calls** from any consumer-facing surface to the legacy mainframe (`HOST/3270`). Always go through the integration broker.
- **Custom encryption.** Use the platform KMS and our standard libraries. Rolling your own crypto is forbidden.
- **Direct database access** to the loyalty system from non-loyalty services. Use the Loyalty API.

## 7. Recommended defaults (should, not must)

- Feature flagging via LaunchDarkly for any change that touches a customer-facing surface
- Server-driven UI for any flow that changes more than monthly
- gRPC for service-to-service; REST only for external/partner APIs
- All new services emit OpenTelemetry traces by default

## 8. Hardware capabilities reference

| Hardware | OS / Platform | Notes |
|---|---|---|
| POS lane | Custom Linux, embedded x86 | Online by default; offline fallback per Section 4 |
| Store handheld (legacy) | Android 7 | Limited APIs per Section 4. Replacement Q1 FY26. |
| Store handheld (new pilot) | Android 13 | Pilot in 50 stores. Build to Android 9+ baseline. |
| Pharmacy workstation | Windows 11, Edge | No mobile patterns; desktop-first UX |
| Customer mobile app | iOS 16+, Android 9+ | Below floor = legacy maintenance mode only |

---

This page is updated quarterly. If your team needs an exception, file an ADR (Architecture Decision Record) tagged `exception:` and route it to the Architecture Review Board.
