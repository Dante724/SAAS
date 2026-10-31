# Flow check: estimate → work order → invoice → pickup (30 Oct 2026)

**Scope:** `roadmap/orderry-parity` at 2590b64. This does not include open PRs #21, #26, #27, #56 or #57.

**How it was checked:**
- An end-to-end API test (`backend/tests/test_flow_estimate_to_invoice.py`) drives one phone repair through the same calls the web app makes.
- Three code reviews: estimates, the job lifecycle, and invoicing/payment.
- Every gap marked **Verified** was reproduced by a test or confirmed in the code. The others come from code reading.

## What works end to end (verified by the flow test)
- Create an estimate (numbered E…) with product and service lines and the right total. It doesn't take stock.
- Send the customer a message with the tracking link. They open the page and see the priced lines.
- **The customer approves with a signature.** The estimate becomes a work order (WO… number) in place: lines, photos, notes and custom fields all carry over, and the part comes out of stock.
- **On the job:**
  - add a task linked to the job;
  - add a photo shared with the customer (it shows on their page);
  - add an internal note (it stays private).
- **Customer messaging:**
  - the customer replies from their page;
  - status changes (in progress → ready → done) email the customer automatically.
- **Invoicing and pickup:**
  - an invoice with the right total;
  - emailing the invoice through the API, with the PDF attached;
  - taking payment, after which the invoice shows paid;
  - closing the job, with the job history recording every step.

## Gaps, most serious first

### Money (fix first)
| # | Gap | Verified | Example |
|---|---|---|---|
| G4 | **A deposit never reaches the invoice** (`cash.py:734` writes the prepayment with no invoice) | **Yes (test)** | $200 job, $50 deposit, $150 paid at pickup: the job shows paid, the invoice stays "partial" at $150/$200 forever, and QuickBooks gets an open invoice |
| F2 | **A payment can land on the wrong invoice.** It goes to the newest open invoice and is capped at the job balance, not that invoice's balance (`order_money.py:69`) | Code | A "collect & invoice" for a $40 item, then the full $300 balance is paid: the $40 invoice shows $300 paid, and the rest gets drafted later as a new, unpaid invoice |
| F3 | **One invoice for several jobs only credits the first job** (`finance.py:663`); the bulk group invoice prints only job A's lines under an A+B total | Code | Job B still shows a balance, can't be closed with "payment required", and its pay link still works, so the customer can pay twice |
| F4 | **The invoice doesn't follow later job changes**: lines are live, the total is stored | Code | A job reopened and a $50 part added: the PDF shows $250 of lines with "Total 200" |
| F5 | **Tax and discount mean a percentage on manual invoices** and an amount everywhere else | Code | A 20% manual invoice prints "Tax 20.00" |

### Things that look done but don't happen
| # | Gap | Verified |
|---|---|---|
| G3 | **"Message the customer" from a job is saved, but no SMS or email is sent.** The app still says "Message sent to client". The two message boxes (Conversation vs Activity) don't see each other's messages | **Yes (test)** |
| R1 | **Publishing a recommendation doesn't notify the customer**, although the app says "Sent to the customer" | Code |
| G1 | **Nobody on staff is told when a customer approves an estimate online** (only an assigned technician gets a push). Customer replies on the tracking page alert no one | **Yes (test)** |
| G2 | **No warranty starts at pickup.** A job line doesn't take the product's or service's warranty days, and the screen doesn't send them | **Yes (test + code)** |
| I6 | **No button to send an invoice.** The email-with-PDF endpoint exists but nothing in the app calls it. No SMS or portal option; invoices never move from draft to "issued" | **Yes (code)** |

### Estimates
| # | Gap |
|---|---|
| E1 | **Anyone can convert an estimate.** `POST /orders/{id}/convert` has no permission check (it is unused by the app) |
| E2 | **A customer can approve or decline over and over.** Each accept adds another signature and fires the "accepted" webhook again |
| E3 | **Approval and conversion skip the usual new-job steps:** new-job notifications and webhook, checklist auto-attach, creator commission, and the job type's own starting status. Customer accept/decline sets the status directly, bypassing status rules |
| E4 | There's no "Convert to work order" or "Send estimate" button; conversion happens only by picking the "Order created" status |
| E5 | **Missing estimate features:**<br>• no "valid until";<br>• no revisions;<br>• no line-by-line approval (only through recommendations);<br>• no deposit request at approval;<br>• no decline reason |

### On the job
| # | Gap |
|---|---|
| J1 | **The "signature required" setting has no screen and there's no in-shop signature pad.** A job type with it switched on can only be closed after the customer signs remotely |
| J2 | **Notes and files have no "internal / customer" switch.** If the shop default is "customer", staff notes show on the tracking page |
| J3 | **Device passcode is never saved** (the field exists in the model, but nothing writes it). There's no intake terms/consent capture |
| J4 | **Tasks aren't shown on the job.** You link a task by typing the job id. The task endpoints don't filter the linked job or client by shop themselves, but the existing shop guard already refuses another shop's ids when a task is saved (tasks have real links to jobs and clients), so nothing leaks today. Add the filter anyway as a second line of defence |
| J5 | **Silent failures:**<br>• side steps on job create and status change (commissions, alerts, webhooks) fail silently; covered by PR #57 (R1);<br>• one unguarded call can error after the job is saved, so a retry creates a duplicate job;<br>• "simulated" sends (no email/SMS set up) look like success |
| J6 | **Small items:**<br>• "Job type" and "Priority" look like dropdowns but can't be changed;<br>• running timers don't stop when a job closes;<br>• QuickBooks gets the wrong lines for sub-invoices and multi-job invoices;<br>• the Xero push is a stub;<br>• the "payment received" message fires only for till payments |

## Suggested fix batches
1. **Invoice money:** one shared "apply payment to invoice" service used by every payment path. It should:
   - carry deposits onto the invoice;
   - route by invoice balance;
   - split across multi-job invoices;
   - recompute the invoice when job lines change;
   - use one meaning for tax and discount.

   Fixes G4, F2–F5 and the multi-job pay-twice risk.
2. **Customer communication:**
   - one "message the customer" endpoint that really sends (SMS/email) and fires the rules, used by both boxes;
   - notify on published recommendations;
   - "Send estimate" and "Send invoice" buttons (email/SMS + link + PDF);
   - alert staff on approvals and customer replies;
   - show "not sent: set up email/SMS" instead of a fake success.

   Fixes G1, G3, R1, I6 and E4.
3. **Estimate → job correctness:**
   - permission on convert;
   - one decision per estimate;
   - run approvals and conversion through the normal status change and new-job steps;
   - warranty from the catalog;
   - in-shop signature and the job-type gate settings screen;
   - internal/customer switch on notes;
   - passcode capture;
   - task screen on the job, with a shop filter on the task read path.

The gap tests in `test_flow_estimate_to_invoice.py` (G1–G4) are marked "expected to fail" (strict). Each fix should remove its mark.

## Not checked
- **Not clicked through in the browser:** this pass used the API test and code reading. UI gaps were confirmed by searching the frontend code.
- **Real delivery:** no real email or SMS was sent, since test mode only simulates sends.
