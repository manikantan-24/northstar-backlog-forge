#!/usr/bin/env python3
"""
Seed a GitHub repo with realistic mock issues for testing the Backlog Synthesizer.

Issues are written in the NorthStar Retail domain (POS, mobile, ecommerce,
pharmacy, loyalty) so the Gap Detector can find meaningful duplicates,
conflicts, and gaps when comparing synthesized stories against this backlog.

Usage:
    python scripts/seed_github_issues.py

Requires:
    GITHUB_TOKEN  — PAT with repo scope
    GITHUB_OWNER  — org or username
    GITHUB_REPO   — repository name

The script is idempotent — it checks for existing issues with the same title
and skips duplicates so it's safe to re-run.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ── Load .env ─────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "")

if not all([GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO]):
    print("ERROR: Set GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO in .env first.")
    sys.exit(1)

if GITHUB_TOKEN.startswith("ghp_your"):
    print("ERROR: Replace the placeholder GITHUB_TOKEN in .env with your real token.")
    sys.exit(1)

# ── Mock issues ────────────────────────────────────────────────────────────────
# 20 realistic backlog issues in the NorthStar Retail domain.
# Mix of epics/stories, different labels, priorities, and states.
MOCK_ISSUES = [
    {
        "title": "POS terminal loses connection during peak hours",
        "body": (
            "## Problem\n"
            "During high-traffic periods, POS terminals intermittently lose "
            "network connectivity, causing transaction failures and customer frustration.\n\n"
            "## Acceptance Criteria\n"
            "- Given the POS is processing a transaction, when network drops, "
            "then the transaction queues locally and syncs when connection restores\n"
            "- Given the queue contains pending transactions, when connectivity "
            "is restored, then all transactions sync within 30 seconds\n\n"
            "## Priority\nHigh\n\n"
            "## Labels\npos, offline-mode, payments"
        ),
        "labels": ["pos", "offline-mode", "payments", "bug"],
    },
    {
        "title": "Mobile app crashes on checkout with loyalty points",
        "body": (
            "## Problem\n"
            "The mobile app crashes when a customer tries to apply loyalty points "
            "during checkout if the points balance is above 10,000.\n\n"
            "## Steps to Reproduce\n"
            "1. Log in with an account having >10,000 loyalty points\n"
            "2. Add items to cart\n"
            "3. Tap 'Apply loyalty points' on checkout screen\n"
            "4. App crashes\n\n"
            "## Acceptance Criteria\n"
            "- Given a customer has >10,000 points, when they apply points, "
            "then checkout completes without crash\n\n"
            "## Priority\nHigh"
        ),
        "labels": ["mobile-app", "loyalty", "bug"],
    },
    {
        "title": "Add barcode scanner support for pharmacy prescription lookup",
        "body": (
            "## Story\n"
            "As a pharmacist, I want to scan a prescription barcode to instantly "
            "pull up patient and medication details, so I can serve customers faster.\n\n"
            "## Acceptance Criteria\n"
            "- Given a valid prescription barcode is scanned, when the scanner reads it, "
            "then the patient record appears within 2 seconds\n"
            "- Given an expired prescription is scanned, when lookup runs, "
            "then a clear expiry warning is shown\n"
            "- Given an invalid barcode, when scan is attempted, then an error "
            "message guides the pharmacist\n\n"
            "## Priority\nMedium"
        ),
        "labels": ["pharmacy", "pos", "feature"],
    },
    {
        "title": "Implement offline cash sale processing at POS",
        "body": (
            "## Story\n"
            "As a store associate, I want to process cash sales when the internet "
            "is down, so I can serve customers without interruption.\n\n"
            "## Acceptance Criteria\n"
            "- Given the POS is offline, when a cash sale is processed, "
            "then the transaction is stored locally with a pending sync flag\n"
            "- Given offline transactions exist, when connectivity is restored, "
            "then all pending sales sync automatically to the central system\n"
            "- Given offline mode is active, when the associate opens the POS, "
            "then a visible offline indicator is displayed\n\n"
            "## Priority\nHigh\n\n"
            "## Notes\nRelated to network resilience epic."
        ),
        "labels": ["pos", "offline-mode", "feature"],
    },
    {
        "title": "Loyalty points not updating after online purchase",
        "body": (
            "## Problem\n"
            "Loyalty points earned from the ecommerce platform are not reflected "
            "in the customer's account for up to 48 hours.\n\n"
            "## Expected Behaviour\n"
            "Points should appear within 15 minutes of purchase confirmation.\n\n"
            "## Acceptance Criteria\n"
            "- Given a customer completes an online purchase, when the order "
            "is confirmed, then loyalty points update within 15 minutes\n\n"
            "## Priority\nMedium"
        ),
        "labels": ["loyalty", "ecommerce", "bug"],
    },
    {
        "title": "Ecommerce product search returns out-of-stock items first",
        "body": (
            "## Problem\n"
            "Search results on the website prioritise out-of-stock items, "
            "causing a poor customer experience.\n\n"
            "## Acceptance Criteria\n"
            "- Given a customer searches for a product, when results load, "
            "then in-stock items appear before out-of-stock items\n"
            "- Given all items in a search are out of stock, when results load, "
            "then a 'notify me' CTA is shown prominently\n\n"
            "## Priority\nMedium"
        ),
        "labels": ["ecommerce", "bug"],
    },
    {
        "title": "Vendor portal: bulk product upload via CSV",
        "body": (
            "## Story\n"
            "As a vendor, I want to upload product listings in bulk via CSV, "
            "so I can onboard my catalogue without manually entering each item.\n\n"
            "## Acceptance Criteria\n"
            "- Given a vendor uploads a valid CSV, when processing completes, "
            "then all products appear in the portal within 10 minutes\n"
            "- Given a CSV has validation errors, when upload is attempted, "
            "then a row-by-row error report is generated and emailed\n"
            "- Given a CSV upload exceeds 10,000 rows, when submitted, "
            "then processing runs as a background job with progress notification\n\n"
            "## Priority\nMedium"
        ),
        "labels": ["vendor-portal", "feature"],
    },
    {
        "title": "Store associate app: shift handover notes",
        "body": (
            "## Story\n"
            "As a store associate, I want to leave shift handover notes in the app, "
            "so the incoming shift knows about open issues and tasks.\n\n"
            "## Acceptance Criteria\n"
            "- Given an associate ends their shift, when they open handover notes, "
            "then they can type and save notes visible to the next shift\n"
            "- Given notes are saved, when the next shift associate logs in, "
            "then a notification prompts them to read the handover notes\n\n"
            "## Priority\nLow"
        ),
        "labels": ["store-associate", "feature"],
    },
    {
        "title": "Inventory: low-stock alerts not triggering for pharmacy items",
        "body": (
            "## Problem\n"
            "The automatic low-stock alert system is not triggering for controlled "
            "pharmacy medications, creating risk of stock-out.\n\n"
            "## Root Cause\n"
            "Pharmacy items use a separate inventory category that is excluded "
            "from the standard alert rules.\n\n"
            "## Acceptance Criteria\n"
            "- Given pharmacy stock drops below the configured threshold, when "
            "the inventory check runs, then an alert is sent to the pharmacy manager\n\n"
            "## Priority\nHigh"
        ),
        "labels": ["inventory", "pharmacy", "bug"],
    },
    {
        "title": "Add Apple Pay and Google Pay to ecommerce checkout",
        "body": (
            "## Story\n"
            "As a customer, I want to pay with Apple Pay or Google Pay, "
            "so I can check out faster without entering card details.\n\n"
            "## Acceptance Criteria\n"
            "- Given a customer selects Apple Pay, when they authorise with Face ID/Touch ID, "
            "then payment processes and order confirmation is shown\n"
            "- Given a customer selects Google Pay, when they complete the flow, "
            "then payment processes without requiring card entry\n"
            "- Given a payment wallet method fails, when the error occurs, "
            "then the customer is prompted to use an alternative payment method\n\n"
            "## Priority\nHigh"
        ),
        "labels": ["ecommerce", "payments", "feature"],
    },
    {
        "title": "Analytics dashboard: daily sales by store",
        "body": (
            "## Story\n"
            "As a regional manager, I want to see daily sales figures per store "
            "in a single dashboard, so I can compare performance without running "
            "individual reports.\n\n"
            "## Acceptance Criteria\n"
            "- Given the manager opens the dashboard, when they select a date range, "
            "then sales are shown per store in a sortable table and bar chart\n"
            "- Given the manager exports the data, when they click export, "
            "then a CSV downloads with store, date, and total columns\n\n"
            "## Priority\nMedium"
        ),
        "labels": ["analytics", "feature"],
    },
    {
        "title": "POS: receipt email option at checkout",
        "body": (
            "## Story\n"
            "As a customer, I want the option to receive my receipt by email "
            "instead of paper, so I can reduce paper waste.\n\n"
            "## Acceptance Criteria\n"
            "- Given the associate completes a sale, when they prompt the customer, "
            "then the customer can choose email receipt, printed receipt, or both\n"
            "- Given email receipt is selected, when the sale is complete, "
            "then the receipt arrives in the customer's inbox within 2 minutes\n\n"
            "## Priority\nLow"
        ),
        "labels": ["pos", "feature"],
    },
    {
        "title": "Ecommerce: guest checkout option",
        "body": (
            "## Story\n"
            "As a first-time customer, I want to check out without creating an account, "
            "so I can complete my purchase quickly.\n\n"
            "## Acceptance Criteria\n"
            "- Given a customer clicks 'Guest checkout', when they enter contact and "
            "payment details, then the order completes without account creation\n"
            "- Given a guest order is placed, when the customer views the confirmation, "
            "then they are offered an easy one-click account creation\n\n"
            "## Priority\nMedium"
        ),
        "labels": ["ecommerce", "feature"],
    },
    {
        "title": "Accessibility: screen reader support for mobile app",
        "body": (
            "## Story\n"
            "As a visually impaired customer, I want the mobile app to be fully "
            "navigable by screen reader, so I can shop independently.\n\n"
            "## Acceptance Criteria\n"
            "- Given VoiceOver (iOS) or TalkBack (Android) is active, when the "
            "customer navigates the app, then all buttons and images have descriptive labels\n"
            "- Given a customer uses the screen reader on the checkout screen, "
            "when they reach the payment section, then all fields are correctly announced\n\n"
            "## Priority\nMedium"
        ),
        "labels": ["mobile-app", "accessibility", "feature"],
    },
    {
        "title": "Performance: POS transaction time > 5 seconds on card payment",
        "body": (
            "## Problem\n"
            "Card payment transactions at the POS are taking 5-8 seconds to complete, "
            "significantly above the 2-second target.\n\n"
            "## Investigation Notes\n"
            "Profiling shows the delay occurs during the fraud-check API call.\n\n"
            "## Acceptance Criteria\n"
            "- Given a card payment is initiated, when the transaction completes, "
            "then the total time from tap to approval is under 2 seconds at p95\n\n"
            "## Priority\nHigh"
        ),
        "labels": ["pos", "performance", "bug"],
    },
    {
        "title": "Vendor portal: real-time inventory sync",
        "body": (
            "## Story\n"
            "As a vendor, I want my inventory levels to update in the portal within "
            "minutes of a sale, so I can manage replenishment accurately.\n\n"
            "## Acceptance Criteria\n"
            "- Given a product is sold in-store, when the transaction is recorded, "
            "then the vendor portal reflects the updated stock level within 5 minutes\n"
            "- Given stock drops to zero, when the last unit is sold, "
            "then the vendor receives an automatic low-stock notification\n\n"
            "## Priority\nMedium"
        ),
        "labels": ["vendor-portal", "inventory", "feature"],
    },
    {
        "title": "Security: enforce MFA for store manager POS login",
        "body": (
            "## Requirement\n"
            "Store manager accounts must use multi-factor authentication when "
            "logging into the POS management console to comply with PCI-DSS requirements.\n\n"
            "## Acceptance Criteria\n"
            "- Given a store manager attempts to log in, when they enter their password, "
            "then they are prompted for a second factor (OTP or authenticator app)\n"
            "- Given MFA is not set up, when the manager first logs in, "
            "then they are forced through MFA setup before accessing any features\n\n"
            "## Priority\nHigh"
        ),
        "labels": ["security", "compliance", "pos"],
    },
    {
        "title": "Loyalty: referral programme — share and earn",
        "body": (
            "## Story\n"
            "As a loyalty member, I want to share a referral link with friends, "
            "so we both earn bonus points when they make their first purchase.\n\n"
            "## Acceptance Criteria\n"
            "- Given a member shares their referral link, when a friend clicks it "
            "and makes a first purchase, then both accounts receive 500 bonus points\n"
            "- Given a referral bonus is awarded, when the points post, "
            "then both parties receive a notification\n\n"
            "## Priority\nLow"
        ),
        "labels": ["loyalty", "mobile-app", "feature"],
    },
    {
        "title": "Inventory: automated reorder based on sales velocity",
        "body": (
            "## Story\n"
            "As a store manager, I want the system to automatically raise a reorder "
            "request when stock falls below a dynamic threshold calculated from "
            "recent sales velocity, so I never run out of fast-moving items.\n\n"
            "## Acceptance Criteria\n"
            "- Given a product's stock falls below its velocity-based threshold, "
            "when the daily inventory check runs, then a draft purchase order is created\n"
            "- Given the draft PO is created, when the manager reviews it, "
            "then they can approve, adjust quantity, or dismiss with a reason\n\n"
            "## Priority\nMedium"
        ),
        "labels": ["inventory", "analytics", "feature"],
    },
    {
        "title": "Ecommerce: personalised homepage recommendations",
        "body": (
            "## Story\n"
            "As a returning customer, I want to see product recommendations based "
            "on my purchase history on the homepage, so I can discover relevant products faster.\n\n"
            "## Acceptance Criteria\n"
            "- Given a logged-in customer visits the homepage, when the page loads, "
            "then a 'Recommended for you' section shows at least 6 relevant products\n"
            "- Given a customer has fewer than 3 past purchases, when they visit, "
            "then trending products are shown instead of personal recommendations\n\n"
            "## Priority\nMedium"
        ),
        "labels": ["ecommerce", "analytics", "feature"],
    },
]

# ── Create issues ──────────────────────────────────────────────────────────────

GREEN  = "\033[92m"; YELLOW = "\033[93m"; RED = "\033[91m"; NC = "\033[0m"

def main():
    try:
        from github import Github, GithubException
    except ImportError:
        print(f"{RED}PyGitHub not installed. Run: pip install pygithub{NC}")
        sys.exit(1)

    print(f"\nConnecting to GitHub as {GITHUB_OWNER}/{GITHUB_REPO}…")
    g = Github(GITHUB_TOKEN)

    try:
        repo = g.get_repo(f"{GITHUB_OWNER}/{GITHUB_REPO}")
        print(f"{GREEN}✓{NC}  Connected: {repo.full_name} "
              f"({'private' if repo.private else 'public'})\n")
    except GithubException as e:
        if e.status == 404:
            print(f"{RED}✗  Repo {GITHUB_OWNER}/{GITHUB_REPO} not found.{NC}")
            print("   Check GITHUB_OWNER and GITHUB_REPO in .env")
        elif e.status == 401:
            print(f"{RED}✗  Authentication failed. Check your GITHUB_TOKEN.{NC}")
        else:
            print(f"{RED}✗  GitHub error: {e}{NC}")
        sys.exit(1)

    # Ensure labels exist
    existing_labels = {lb.name for lb in repo.get_labels()}
    needed_labels = {
        "pos": "0075ca",
        "mobile-app": "7057ff",
        "ecommerce": "008672",
        "loyalty": "e4e669",
        "inventory": "d876e3",
        "pharmacy": "0e8a16",
        "vendor-portal": "fbca04",
        "store-associate": "c5def5",
        "analytics": "84b6eb",
        "payments": "b60205",
        "offline-mode": "f9d0c4",
        "accessibility": "bfdadc",
        "performance": "e99695",
        "security": "d93f0b",
        "compliance": "1d76db",
        "bug": "d73a4a",
        "feature": "a2eeef",
    }
    for name, colour in needed_labels.items():
        if name not in existing_labels:
            try:
                repo.create_label(name=name, color=colour)
                print(f"  Created label: {name}")
            except GithubException:
                pass  # label may exist in different casing

    # Get existing issue titles to avoid duplicates
    print("Checking for existing issues…")
    existing_titles = {i.title.lower() for i in repo.get_issues(state="all")}
    print(f"  Found {len(existing_titles)} existing issue(s)\n")

    created = 0
    skipped = 0

    for issue_data in MOCK_ISSUES:
        title = issue_data["title"]
        if title.lower() in existing_titles:
            print(f"  {YELLOW}→{NC}  Skipped (exists): {title[:70]}")
            skipped += 1
            continue

        try:
            # Only apply labels that exist in the repo
            labels = [lb for lb in issue_data.get("labels", [])
                      if lb in needed_labels]
            issue = repo.create_issue(
                title=title,
                body=issue_data["body"],
                labels=labels,
            )
            print(f"  {GREEN}✓{NC}  Created #{issue.number}: {title[:70]}")
            created += 1
            time.sleep(0.5)  # avoid secondary rate limit
        except GithubException as e:
            print(f"  {RED}✗{NC}  Failed '{title[:50]}': {e}")

    print(f"\n{'─'*60}")
    print(f"  {GREEN}{created} issue(s) created{NC}  ·  {YELLOW}{skipped} skipped{NC}")
    print(f"  View at: {repo.html_url}/issues")
    print()

    if created > 0:
        print(f"  {GREEN}Done!{NC} Now test the GitHub MCP integration:")
        print(f"  source venv313/bin/activate")
        print(f"  set -a && source .env && set +a")
        print(f"  GITHUB_MCP_ENABLED=1 python scripts/test_mcp_tools.py")


if __name__ == "__main__":
    main()
