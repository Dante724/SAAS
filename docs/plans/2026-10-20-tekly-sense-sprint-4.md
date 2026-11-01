# Tekly Sense — Sprint 4: "It chases for me" (20 Oct 2026)

Same council (PM, engineer, tester, security), TDD, browser check. No AI service, no new
dependency. Messages go through the shop's own SMS / WhatsApp / email setup, as today.

## The three agents
| Agent | Who it messages | When |
|---|---|---|
| Collections | Customers with an overdue invoice | N days after the due date (default 3) |
| Quote follow-up | Customers with a quote not yet answered | N days after it was made (default 2), quotes up to 30 days old |
| Win-back | Customers with **marketing consent** who haven't been in for N months (default 9) | Up to a year past that, then they're left alone |

## Safe by default
- **Off** until the owner turns each one on. When on, every message is a **draft** waiting
  for the owner's OK ("Ask me before sending"). Sending without asking is a separate
  switch per agent.
- **Consent:**
  - checked when a message is drafted, and again at send time (STOP or unsubscribe in
    between wins);
  - collections and quotes need the channel's consent;
  - win-back also needs marketing consent (the latest "marketing" entry in the consent log
    must be a yes).
- **Quiet hours:** sent only inside the location's opening hours, in the shop's own time
  zone. Outside them, a message waits for the next opening instead of going out.
- **Caps:**
  - per customer: 1 agent message per 72 hours, 3 per 30 days;
  - per invoice or quote: a reminder at most every 7 days, 2 in total;
  - win-back: once per 180 days per customer;
  - per shop: a daily cap the owner sets (default 50).
- **Kill switch:** an approved message is skipped at send time if Tekly Sense, the agent, or
  the platform switch `SENSE_AGENTS_DISABLED` is off.
- **Unsubscribe:**
  - every agent email ends with a one-click unsubscribe link (a signed token, no login);
  - the link opens a confirm page, and only the button changes anything, so link scanners
    can't unsubscribe people;
  - unsubscribing turns off email and marketing and is written to the consent log;
  - texts keep the existing "Reply STOP to opt out" on the first message.
- **Who approves:** the shop owner, or a role with the new opt-in permission "Approve
  automatic customer messages" (Full access template only). Approvals are audited with
  counts only, never message text or numbers.

## Where
- Settings → General → Tekly Sense → Customers: on/off, "Ask me before sending", wait time
  and channel per agent, plus the daily cap.
- Today: "Messages waiting for your OK", with Send and Discard for one or all.

## Tests first
- Off by default.
- Drafts aren't sent; approval sends them.
- Auto-send.
- Every cap.
- Consent at draft time and at send time.
- Marketing consent for win-back.
- Kill switch at send time.
- Quiet hours reschedule.
- Unsubscribe: a GET doesn't change anything, the POST does, and a tampered token fails.
- Approver permission.
- Tenant isolation of drafts.

## Review outcomes (folded in before commit)
Security and compliance review: approve with changes. Both High findings are fixed.
- **Stale messages:** at send time each message re-checks that the invoice is still owed or
  the quote still open. Drafts not approved within 7 days expire.
- **Marketing law:**
  - win-back is email only (US marketing texts need written consent per channel);
  - every win-back email carries the shop's name, postal address and contact, and nothing
    goes out without an address;
  - texts keep the STOP line;
  - WhatsApp is not offered until approved templates exist.
- **Hours:** each location's own opening hours, in the shop's time zone. Closed days are
  skipped, and marketing never goes out on a Sunday.
- **Email headers:** agent emails carry RFC 8058 one-click `List-Unsubscribe` headers.
- **Unsubscribe page:**
  - no-referrer, no-store and noindex headers;
  - unsubscribing twice records it once;
  - the wording now says it stops all email from the shop.
- **Smaller fixes:**
  - customer names lose markup characters;
  - draft ids are length-capped;
  - earlier messages are read once per run (180 days);
  - marketing consent is read in one query.

Tester review: ship after two small fixes. 29 edge tests are now in `test_sense_sprint4_edge.py`.
- A shop with one location uses its hours even for invoices with no job.
- The approval test no longer depends on the real date.
- Collections now only chases invoices up to 120 days overdue, and reminds the payer when
  someone else pays.

The full test run also found that "service due" counted visit days in UTC but "today" in the
shop's time zone, which was off by one near midnight. Visit days now use the shop's calendar.

Browser check (demo data):
- Turning Collections on kept "Ask me before sending" ticked.
- The draft showed the balance still owed (₹3,200 of ₹4,200) with the STOP line.
- Pressing Send scheduled it for the shop's next opening (09:00 Kolkata), and the audit
  recorded counts only.
