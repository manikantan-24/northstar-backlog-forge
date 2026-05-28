# NorthStar Retail — Q3 Product Strategy (Customer Experience Track)

**Author:** Priya Rao, VP Customer Experience
**Status:** Draft for executive review
**Target:** Q3 FY26 (July – September)
**Audience:** Engineering, product, and store operations leadership

---

## Background

In Q2 we improved e-commerce conversion 11% through search relevance and checkout streamlining. Customer satisfaction scores held flat. The next inflection point will come from solving the customer-experience problems that show up *in stores* and *between channels* — places where our two largest customer segments (multi-channel shoppers, pharmacy-dependent customers) feel friction every visit.

Voice-of-customer data from the last 90 days converges on five themes. Three of them are P0 for Q3. Two are P1 candidates depending on capacity.

---

## P0 — Must ship by end of Q3

### 1. POS resilience under network failure

**The problem.** When a store's WAN connection drops, the lane cannot process any transaction — not even cash. Three Houston-cluster stores were down for 40 minutes during a Saturday afternoon rush in March. Estimated direct revenue loss: $42K across the three stores. Indirect: customers who left and didn't return.

**The goal.** Cash sales, gift card redemption, and returns under $50 continue to function during a WAN outage at every lane. Card sales remain online-only per PCI.

**Success metric.** Zero "lane unavailable" minutes during the next major WAN incident, measured at the next reported outage. Synthetic test: pull the WAN, ring up a cash sale, return a gift card transaction, all within 90 seconds with no error to the customer.

### 2. Pharmacy refill unification

**The problem.** Customers can request a refill via the mobile app OR by calling the IVR phone line. These two intake channels write to different systems with no real-time reconciliation. Customers regularly arrive at the pharmacy expecting their prescription is ready when it isn't, or vice versa. The pharmacy help line spends an estimated 18% of its volume just disambiguating refill status.

**The goal.** Both channels become writes to **Rx Hub**, the system of record. The patient sees a single status (queued / ready / picked up) regardless of how they initiated. Status changes trigger a push notification to the verified contact method.

**Constraints.** HIPAA-compliant notifications only: opt-in stored on the prescription, sent to the patient's verified contact (not household default), audit log retained 7 years.

**Success metric.** Refill-status help-line volume drops by 50% within 60 days of launch.

### 3. Mobile app search — local inventory awareness

**The problem.** Search ranking does not factor local inventory. Customers search at home, see in-stock results, drive to the store, find them missing, and leave frustrated. NPS for customers who hit this is -30; for customers who don't it's +18.

**The goal.** Search results are reranked by availability at the customer's home store. When the top result is out of stock locally, surface in-stock alternatives inline (not at checkout).

**Constraints.** Pricing must not be personalized based on inventory state without disclosure (Legal). Stay under the 800ms p95 search latency budget.

**Success metric.** 30-day rolling NPS for searches that lead to add-to-cart improves to +10 minimum from the current -8 chain-wide average.

---

## P1 — Stretch goals for Q3

### 4. Loyalty tier progress transparency

A modest UX change. Customers don't understand the rules of tier earning and tier downgrade. We add a "tier progress" view in the mobile app that shows current points, distance to next tier, and the date of the next evaluation. Existing customer feedback suggests this lands well even as a small change.

**Success metric.** Customer support contact rate about "why was I downgraded" drops 60% within 90 days of launch.

### 5. Store-associate handheld experience

Our 7-year-old handheld scanners (Android 7) limit what we can build for store associates. Hardware refresh is approved for FY26 but won't ship until Q1. In Q3 we should ensure any *new* tooling we build is gated by hardware capability — Android 9+ for the consumer mobile app baseline, with explicit fallbacks for the legacy fleet.

This is more a discipline than a feature: every new mobile-app-touching story must declare its hardware floor.

---

## Out of scope for Q3 (explicitly)

- **Self-checkout improvements.** Capital project owned by Ops, not engineering.
- **Vendor portal redesign.** Slated for FY27 H1.
- **B2B membership tier.** Still in research; no decision before Q4.
- **Spanish-language mobile app.** In flight (NS-096) but not a Q3 commitment.

---

## Cross-cutting expectations

- Every Q3 story must trace to a customer-facing outcome or a constraint forcing function. Pure tech-debt items continue to be funded out of the engineering capacity reserve, not the OKR-attributed capacity.
- Compliance review (Legal + InfoSec) is mandatory for: refill notifications (HIPAA), price personalization (advertising), and any new payment flow (PCI).
- Architecture Review Board (ARB) sign-off required for any deviation from the constraints in the engineering architecture wiki.

---

## What I'm asking engineering to do next

1. Synthesize this strategy into a structured Q3 backlog: epics → stories → tasks.
2. Cross-check against the existing JIRA/GitHub items so we don't redo work that's already planned.
3. Surface any **gaps** — capabilities I implied here that neither this strategy nor the existing backlog covers.
4. Surface any **conflicts** with the architecture constraints early.

I want this back by the next steering meeting in two weeks so we can sequence the Q3 calendar.
