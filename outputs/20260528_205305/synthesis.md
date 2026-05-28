# Backlog Synthesis

*Synthesized from: case_02_pharmacy_escalation.txt*

## Summary

Pharmacy refill experience is generating significant customer complaints, primarily due to unreliable in-app notifications and incorrect notification recipient targeting. The team discussed implementing proactive, patient-scoped push notifications while addressing HIPAA compliance, and prioritizing existing SMS reminder work.

## Epics (1)

### Epic 1: HIPAA-Compliant Pharmacy Notifications

This epic focuses on developing and enhancing prescription-ready notification systems for pharmacy patients, ensuring all delivery methods (push, SMS) adhere strictly to HIPAA compliance requirements for patient privacy and data security. It includes refactoring existing notification services to support patient-scoped delivery and implementing explicit opt-in mechanisms.

#### 1.1 Enable proactive push notifications for prescription ready status

**Priority:** High   |   **Tags:** `pharmacy` `mobile-app` `compliance` `notifications`

> Customers currently only see prescription-ready notifications when they open the app. This story adds proactive push notification delivery when a prescription status changes to ready-for-pickup. HIPAA compliance requires explicit patient opt-in stored on the prescription record and delivery only to the patient's verified contact method, never the household account default. Constraint C-05 mandates opt-in, verified contact delivery, and 7-year audit retention.

**User story**
- As a pharmacy patient, I want to receive a push notification on my mobile device when my prescription is ready for pickup, so that I can plan my store visit without opening the app.

**Acceptance criteria**
- Given a prescription status changes to 'ready-for-pickup' in Rx Hub, when the patient has opted in for notifications on that prescription, then a push notification is sent to the patient's verified device within 60 seconds.
- Given a patient has not opted in for notifications on a prescription, when that prescription becomes ready, then no push notification is sent.
- Given a push notification is sent, when the notification is delivered, then an audit log entry is written containing timestamp, patient ID, prescription ID, delivery method, and success/failure status.
- Given a household account has multiple patient identities, when a prescription is ready for patient A, then the notification is sent only to devices authenticated as patient A, not to patient B's devices or the household account default contact.
- Given the audit log for pharmacy notifications, when queried, then records are retained for at least 7 years per HIPAA requirements.

**Tasks**
- ST-01-TK-01: Design and implement Rx Hub API and data model for per-prescription patient push notification opt-in.
- ST-01-TK-02: Modify Rx Hub 'prescription ready' event publisher to include opt-in status and verified patient contact methods.
- ST-01-TK-03: Develop a new push notification trigger in the Notification Service, conditional on patient opt-in status.
- ST-01-TK-04: Integrate with NSID service to resolve patient-verified device tokens for push delivery.
- ST-01-TK-05: Implement audit logging for push notification delivery, including HIPAA-mandated fields and 7-year retention.
- ST-01-TK-06: Develop mobile app UI for patient explicit opt-in/opt-out of push notifications for individual prescriptions.
- ST-01-TK-07: Write end-to-end tests for push notification delivery with various opt-in/opt-out and multi-patient scenarios.

#### 1.2 Implement patient-scoped notification routing in notification service

**Priority:** High   |   **Tags:** `pharmacy` `mobile-app` `compliance` `identity` `notifications`

> The current notification system routes alerts to household accounts rather than individual patient identities, causing family members to receive each other's prescription notifications. This story refactors notification delivery to route based on patient identity (via NSID) rather than household account. The patient identity must be the one associated with the prescription in Rx Hub, and delivery must target only devices authenticated as that specific patient.

**User story**
- As a pharmacy patient in a multi-person household, I want notifications for my prescriptions to arrive only on my authenticated devices, so that my family members do not see my private health information.

**Acceptance criteria**
- Given a prescription belongs to patient identity P1 in Rx Hub, when a notification is triggered, then the notification service queries NSID to resolve P1's verified contact methods and authenticated devices.
- Given patient P1 is logged into device D1 and patient P2 is logged into device D2 under the same household account, when a notification for P1's prescription is sent, then only device D1 receives the notification.
- Given a patient identity has no authenticated devices, when a notification is triggered, then the notification is queued and delivered when the patient next authenticates, or falls back to the patient's verified SMS contact if opted in.
- Given the notification service receives a prescription-ready event from Rx Hub, when the event contains a patient identity, then the service must resolve that identity to a verified contact method before attempting delivery.

**Tasks**
- ST-02-TK-01: Refactor Notification Service ingestion layer to correctly parse and validate patient identity (NSID) from events.
- ST-02-TK-02: Develop Notification Service component to query NSID for authenticated devices and verified contact methods by patient identity.
- ST-02-TK-03: Modify Notification Service routing logic to target specific patient devices based on NSID resolution.
- ST-02-TK-04: Implement a queuing mechanism for notifications where no authenticated device is immediately available.
- ST-02-TK-05: Define and implement fallback logic for SMS delivery if patient has opted in and no app device is available.
- ST-02-TK-06: Update Notification Service API documentation to reflect patient-scoped routing capabilities.
- ST-02-TK-07: Write comprehensive unit and integration tests for patient identity resolution and routing.

#### 1.3 Prioritize SMS refill-ready notifications for non-app users

**Priority:** Medium   |   **Tags:** `pharmacy` `sms` `compliance` `notifications` `accessibility`

> Pharmacy customers who do not use the mobile app, particularly older demographics, have no timely notification when prescriptions are ready. Backlog item NS-176 already exists to address SMS delivery of refill-ready notices. This story is to prioritize NS-176 and ensure it respects HIPAA opt-in and verified contact requirements (C-05). The source material indicates this is an existing work item needing reprioritization rather than new discovery.

**User story**
- As a pharmacy customer who does not use the mobile app, I want to receive an SMS when my prescription is ready for pickup, so that I know when to visit the pharmacy without calling.

**Acceptance criteria**
- Given a prescription status changes to 'ready-for-pickup' in Rx Hub, when the patient has opted in for SMS notifications and has a verified SMS contact on record, then an SMS is sent within 60 seconds.
- Given the SMS content for a prescription-ready notice, when the message is composed, then it complies with HIPAA (e.g. 'Your prescription at NorthStar Pharmacy on Main St is ready for pickup' with no medication name unless explicitly opted in).
- Given an SMS is sent, when delivery occurs, then an audit log entry is written containing timestamp, patient ID, prescription ID, phone number (last 4 digits), and delivery status, retained for 7 years.
- Given a patient has opted in for both push and SMS notifications, when a prescription is ready, then both channels deliver notifications independently.

**Tasks**
- ST-03-TK-01: Review and assess existing backlog item NS-176 to understand current state and identify compliance gaps.
- ST-03-TK-02: Enhance Rx Hub to capture and store patient explicit opt-in preferences for SMS notifications per prescription.
- ST-03-TK-03: Modify Notification Service to trigger SMS delivery for 'prescription ready' events based on opt-in and verified SMS contact.
- ST-03-TK-04: Implement HIPAA-compliant SMS message templating, ensuring no medication names unless explicitly opted in.
- ST-03-TK-05: Integrate SMS audit logging for delivery status, including patient ID, last 4 digits of phone number, and 7-year retention.
- ST-03-TK-06: Configure and test SMS gateway integration for high-volume, secure delivery.
- ST-03-TK-07: Develop and execute a comprehensive regression test plan for SMS notification flows.

---
