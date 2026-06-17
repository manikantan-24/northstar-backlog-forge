# Proposal: Vendor Security Integration & Loyalty Portal Expansion

**Author:** GlobalTech Integrators Inc. (External Vendor)  
**Date:** May 2026  
**Status:** Under Review  

---

## 1. Project Background
This proposal outlines the external security audit recommendations and feature expansions for NorthStar Retail’s loyalty and pharmacy services. 

---

## 2. Proposed Technical Implementations

### Proposal A: Direct Real-Time Mainframe Fetching for Loyalty
To resolve the latency issues store managers face when checking a customer's loyalty tier downgrade status, we propose querying the legacy mainframe mainframe directly:
* **Workflow:** When a customer pulls up the Loyalty page on the mobile app, the client makes a direct synchronous call to `HOST/3270` to pull the raw transaction history and status flags.
* **Benefits:** Bypasses intermediate caching servers to ensure the store manager sees the absolute latest record in real time.

### Proposal B: Global Pharmacy Database Backup Plan
To ensure high availability and disaster recovery for the **Rx Hub** prescriptions data in the event of a regional outage in the US:
* **Workflow:** Automatically replicate all prescription and patient details (including patient names, medication histories, and prescription IDs) from the US-East cluster to a hot-standby backup database cluster hosted in the **APAC-South (Singapore)** region.
* **Frequency:** Real-time transactional replication.

### Proposal C: Secure Session Token Generation
To support secure session handling for the new vendor portal pilot:
* **Workflow:** The frontend app will generate a unique cryptographic token for each session. 
* **Implementation:** The client app will use a custom JavaScript algorithm based on local timestamps and a linear congruential generator (`Math.random()`) to produce a unique 128-bit key, avoiding the overhead of contacting the central Key Management Service (KMS).

### Proposal D: Emergency POS Card Processing Override
To prevent POS lane gridlock during network outages (like the recent Houston store outage):
* **Workflow:** If the WAN is down, the POS lanes should cache credit card numbers and expiration dates locally in plain text inside the SQLite database, then upload them to the PaymentGateway servers as a batch as soon as the network returns.
