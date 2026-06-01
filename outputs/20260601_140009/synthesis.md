# Backlog Synthesis

*Synthesized from: case_02_pharmacy_escalation.txt*

## Summary

Pharmacy refill notifications are causing customer complaints due to unreliability and privacy issues. Key concerns include notifications only firing on app open and being sent to household accounts instead of specific patients, raising HIPAA compliance risks. The team needs to implement proactive, patient-scoped push notifications and prioritize existing SMS reminder work, while carefully adhering to HIPAA rules regarding medication names.

## Epics (1)

### Epic 1: Pharmacy Prescription Readiness Notifications & Compliance

This epic consolidates efforts to proactively notify pharmacy customers when their prescriptions are ready for pickup, across mobile push and SMS channels. It also focuses on ensuring strict compliance with patient privacy (HIPAA) requirements, especially for accurate patient-scoped delivery within multi-member households.

#### 1.1 Send proactive push notification when prescription is ready for pickup

**Priority:** High   |   **Tags:** `mobile-app` `pharmacy` `compliance`

> Customers currently only learn a prescription is ready when they open the app and the home screen polls the Rx service. This leads to lost revenue and customer churn when customers never open the app or pick up at competitors who sent proactive reminders. This story requests a proactive push notification when a prescription is ready. Implementation must respect HIPAA requirements for patient opt-in, verified contact methods, and audit logging (see T-02, T-03).

**User story**
- As a pharmacy customer, I want to receive a push notification when my prescription is ready for pickup, so that I am reminded to pick it up and don't go to a competitor instead.

**Acceptance criteria**
- Given a prescription becomes ready for pickup, when the Rx service marks it ready, then a push notification is sent to the patient's verified device within 5 minutes.
- Given a patient has not explicitly opted in to medication-name notifications, when the push is sent, then it contains a generic message such as 'Your prescription is ready' without the medication name.
- Given a patient has opted in to medication-name notifications, when the push is sent, then the medication name may be included and an audit log entry is created.
- Given multiple family members share a household account, when a prescription is ready, then the push goes only to the device logged in as that specific patient identity, not to other household members.
- Given a patient has no verified push contact method, when a prescription is ready, then no push notification is sent and the system logs the reason.

**Tasks**
- ST-01-TK-01: Develop Rx service integration to trigger push notifications upon prescription readiness.
- ST-01-TK-02: Implement notification service logic to check patient opt-in for medication name display.
- ST-01-TK-03: Develop audit logging for all push notifications containing medication names.
- ST-01-TK-04: Implement generic push notification message for non-opted-in patients.
- ST-01-TK-05: Configure push notification payload for delivery via mobile app client.
- ST-01-TK-06: Create automated tests for push notification content based on opt-in status and delivery latency.

#### 1.2 Implement patient-scoped notification delivery for pharmacy notifications

**Priority:** High   |   **Tags:** `mobile-app` `pharmacy` `compliance` `security`

> Pharmacy notifications currently fire to the household account rather than the specific patient, causing privacy violations and HIPAA non-compliance. The Phoenix store incident where a wife received a notification about her husband's medication illustrates the problem. This story addresses the verified contact method requirement: push notifications must go only to the device logged in as the specific patient identity through NSID.

**User story**
- As a pharmacy patient in a multi-member household, I want push notifications about my prescriptions to go only to my device, so that my health information remains private and HIPAA requirements are met.

**Acceptance criteria**
- Given a patient identity is established through NSID login on a specific device, when a pharmacy notification is triggered for that patient, then the push is routed only to that device.
- Given a household account has multiple members, when a pharmacy notification is sent, then it does not appear on devices logged in as other household members.
- Given a patient is not logged in on any device, when a pharmacy notification is triggered, then the notification is queued or an alternative delivery method is used, and the system logs the reason push was not sent.
- Given a notification is sent to a patient-scoped device, when the notification is delivered, then an audit log entry records the patient identity, device identifier, and timestamp.

**Tasks**
- ST-02-TK-01: Refactor notification service to resolve patient NSID to specific device tokens.
- ST-02-TK-02: Update Rx service to pass patient's NSID for targeted notification requests.
- ST-02-TK-03: Develop logic to prevent notifications from being sent to non-patient devices within household accounts.
- ST-02-TK-04: Implement queuing or alternative delivery fallback for notifications if no patient-scoped device is active.
- ST-02-TK-05: Implement detailed audit logging for patient-scoped notification delivery, including device identifiers.
- ST-02-TK-06: Review and update relevant IAM policies for secure access to device token data.
- ST-02-TK-07: Create automated tests for patient-scoped notification routing and exclusion in multi-member households.

#### 1.3 Prioritize existing SMS refill reminder story (NS-176)

**Priority:** Medium   |   **Tags:** `pharmacy` `compliance`

> Anika identified that pharmacy customers over 65 often do not use the mobile app, creating a gap in refill-ready notifications for this segment. An existing backlog story, NS-176, already addresses pharmacy refill SMS reminders and is currently in backlog status. Rather than creating a new story, the team agreed to prioritize NS-176. This story serves as a placeholder to ensure the topic is tracked; the actual implementation will be managed through NS-176.

**User story**
- As a pharmacy customer who does not use the mobile app, I want to receive SMS notifications when my prescription is ready for pickup, so that I am reminded to pick it up in a format I regularly check.

**Acceptance criteria**
- Given a patient has a verified phone number on file, when their prescription is ready for pickup, then an SMS notification is sent to that number.
- Given a patient has opted out of SMS notifications, when their prescription is ready, then no SMS is sent.
- Given a patient has opted in to medication-name SMS, when the SMS is sent, then the medication name may be included and an audit log entry is created.
- Given a patient has not opted in to medication-name SMS, when the SMS is sent, then it contains a generic message without the medication name.

**Tasks**
- ST-03-TK-01: Review and estimate existing backlog story NS-176 for SMS refill reminders.
- ST-03-TK-02: Implement integration with chosen SMS gateway service.
- ST-03-TK-03: Develop backend logic to manage SMS opt-in/opt-out preferences.
- ST-03-TK-04: Implement logic for generic vs. medication-name specific SMS content based on patient opt-in.
- ST-03-TK-05: Develop audit logging for SMS notifications containing medication names.
- ST-03-TK-06: Update database schema to store patient SMS notification preferences.
- ST-03-TK-07: Create automated tests for SMS content, opt-in/out logic, and delivery.

---

## 🔍 Gaps detected

Capabilities implied by the source material that are not represented in the existing backlog.

- **SMS "Prescription Ready for Pickup" Notification** — While ST-01 addresses push notifications for when a prescription is ready for pickup, and ST-03 (via NS-176) prioritizes SMS for refill reminders, there is no explicit capability for sending SMS notifications to customers when their prescription is ready for pickup, especially for non-app users.
  - *Evidence:* Anika identified that pharmacy customers over 65 often do not use the mobile app, creating a gap in refill-ready notifications for this segment.
- **Patient-Scoped Delivery for SMS Pharmacy Notifications** — ST-02 addresses patient-scoped delivery for push notifications to prevent privacy violations and HIPAA non-compliance. However, the problem statement regarding pharmacy notifications firing to household accounts (illustrated by the Phoenix incident) implies that this patient-scoped delivery should also apply to SMS pharmacy notifications (like NS-176).
  - *Evidence:* Pharmacy notifications currently fire to the household account rather than the specific patient, causing privacy violations and HIPAA non-compliance. The Phoenix store incident where a wife received a notification about her husband's medication illustrates the problem.

## ♻️ Possible duplicates

New stories that overlap with existing JIRA / GitHub tickets.

- **Story ST-01** overlaps with **NS-176** (confidence: low)
  - Local-embedding cosine similarity 0.68 — new story title/description overlaps existing ticket "Pharmacy refill: SMS reminder when rx is due".
- **Story ST-03** overlaps with **NS-176** (confidence: low)
  - Local-embedding cosine similarity 0.60 — new story title/description overlaps existing ticket "Pharmacy refill: SMS reminder when rx is due".
