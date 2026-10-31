# Tekly Go: two-day shop pilot runbook

## Why we're running this

The real Tekly Go is going to be a native Expo app. Before we build it, we test the riskiest
assumption: **do bench techs actually pull out their phone to update jobs?** Or is Go really a
counter-and-owner app?

**Decision rule.** Look at the share of status changes on pilot jobs that were made on a phone,
counted over day 2 (or the whole pilot).

- **50% or more:** techs use the phone. The Expo app stays centred on **scan + timer**.
- **Under 50%:** the Expo app is re-centred on **counter + owner**: search, check-in and push.

The report works this out for you (see "Reading the report").

---

## 0. Before you go: what the shop needs

> **The shop's phones must reach a deployed Tekly over HTTPS: staging or production.**
> `localhost` and `http://192.168.x.x` won't work.
>
> - A phone can't reach your laptop's `localhost`.
> - iOS and Android only allow the camera (`getUserMedia`), the service worker and "Add to Home
>   Screen" on a secure (HTTPS) origin. Without HTTPS, Go's Scan screen can't open the camera.
>
> Use the shop's own production tenant if they're already a customer. Otherwise use a staging
> tenant with HTTPS (for example `https://staging.tekly.app`).

Checklist on that deployment:

- [ ] It runs a build that includes this pilot kit: migration `gp144g4` applied, plus the Go
      PWA (`/go`) and `frontend-react` built.
- [ ] **`PUBLIC_BASE_URL` is set** to the HTTPS address phones will use, for example
      `https://shop.tekly.app`. The QR on each label is `PUBLIC_BASE_URL/go/job/<id>?via=qr`.
      If it isn't set, labels use whatever address the person printing was on. That might be an
      internal address the phones can't reach.
- [ ] Open `https://<host>/go` on your own phone, sign in, and scan one test label (section 3)
      before you go to the shop.
- [ ] If the shop has "shop-only network access" turned on, the phones must be on the shop's
      Wi-Fi. Otherwise their changes wait in the offline queue.

---

## 1. Setup (about 45 minutes, the day before or first thing on day 1)

### 1.1 Staff accounts

Every person in the pilot needs **their own** Tekly account. The report counts per person, so a
shared login makes the data useless.

- **Settings → Staff:** add each tech and each counter person, using their own email.
- **Roles:**
  - **Techs:** must be able to change job status. Give them the photo and message permissions
    you want them to use. "Send SMS" (`can_send_sms`) is needed for messaging from Go.
  - **Counter staff:** must have "Create tickets" (`can_create_tickets`) to print labels.
  - **The owner/admin pulling the report:** needs "Manage settings" (`can_manage_settings`).
    The shop owner always has it.
- **Assign the pilot jobs to techs** (each job's Assignee), so "all open jobs for Ana" works.

### 1.2 Install Go on each phone

Send everyone the link `https://<host>/go` (text it, or show it as a QR on your screen).

**iPhone (Safari only, not Chrome):**

1. Open the link in **Safari** and sign in.
2. Tap the **Share** button, then **Add to Home Screen**, then **Add**.
3. Open Tekly Go **from the home-screen icon** and sign in again. On iOS the home-screen app
   keeps its own sign-in, separate from Safari.
4. Tap **Scan** once and allow camera access.

> **iPhone caveat.** Pointing the iPhone **Camera app** at a label opens the job in **Safari**,
> not in the home-screen app.
>
> - That works, but only if they are also signed in to Tekly in Safari.
> - If they aren't, they'll be sent to sign-in, then back to the job.
>
> Tell iPhone techs to **scan from Go's own Scan tab**. It's faster, and it's what we're testing.

**Android (Chrome):**

1. Open the link in **Chrome** and sign in.
2. Tap **⋮**, then **Install app** (or **Add to Home screen**), then **Install**.
3. Open Tekly Go from the home screen and tap **Scan**. Allow camera access.
4. On most Android phones, scanning a label with the camera app (or Google Lens) opens the
   installed Go app directly.

**Check each phone:** open any job in Go and change nothing. If you can see the job, they're
set up.

### 1.3 Print the labels

Pick about **10 live tickets** that will be worked on during the two days. A mix of techs is
good.

In the web app, go to **Work orders**. Then either:

- **Pick jobs.** Tick the jobs, then click **Print QR labels** in the blue bar that appears.
- **All open jobs for one tech.** Click **Filter** and choose the **Assignee**, then click
  **Print QR labels for all their open jobs**.

A PDF opens. The sheet is A4, or Letter if the browser is set to US/Canada.

- Each label is 64 × 38 mm.
- On the left of each label is a QR that opens the job in Tekly Go.
- On the right are the WO number, the customer's first name and the device.

Print at **100% / Actual size** ("Fit to page" shrinks the QR) on plain paper or label stock.
Cut the labels out and tape one to each ticket or bin.

**Printing a label is what makes a job a "pilot job" for the report.** Re-printing is fine.

Developers can also print over the API:
`GET /api/pilot/job-labels?order_ids=<id>,<id>&paper=a4|letter`, or
`?assigned_to_id=<employee id>` instead of ids.

---

## 2. What to tell people

### Techs (2 minutes at the bench)

> "For the next two days, these 10 jobs have a QR sticker. When you change one of them (start
> it, waiting for parts, ready), try doing it on your phone: open Tekly Go, tap **Scan**, point
> it at the sticker, and change the status there. Same for photos: take them from the job in Go.
> If the phone is a pain (gloves, ESD, no pocket, battery), just use the computer like normal
> and tell us why. That's exactly what we want to know. There's no right answer."

Don't push. We're measuring what they **choose** to do. Nagging inflates the number and
defeats the pilot.

### Counter staff

> "Work exactly as you normally do. We're just watching how jobs get looked up when a customer
> walks in or calls: name, phone, ticket number. Nothing changes for you."

---

## 3. Day by day

### Day 0 (setup, the day before)

- [ ] Deployment checklist (section 0) done, and one test label scanned on your own phone.
- [ ] Accounts and roles created (1.1).
- [ ] Note the **employee ids of the techs**, for the headline filter later. Use Settings →
      Staff, or the report JSON's `employees[].employee_id` after day 1.

### Day 1

- [ ] Morning: install Go on each phone (1.2) and print and tape the labels (1.3).
- [ ] Give the tech and counter briefings (section 2).
- [ ] Mid-morning: do one **30-minute counter observation** (section 4).
- [ ] Late afternoon: open **Reports → Go pilot** and check that numbers are appearing.
  - **Status changes are 0 but techs say they used the phone?** Check that they're on the HTTPS
    host, and look at their Go's More screen for "changes waiting to sync".
- [ ] Ask each tech one question: "What got in the way of using the phone?" Write the answers
      down.

### Day 2

- [ ] Morning: replace any labels that went missing, and label new jobs if some pilot jobs closed.
- [ ] Do a second **30-minute counter observation**, at a different time of day (for example
      the after-lunch pickup rush).
- [ ] End of day: pull the report for **day 2 alone** and for **both days** (section 5).
- [ ] Download the CSV and save it with the observation sheets and the techs' answers.
- [ ] Untape the labels, or leave them; they're harmless.

---

## 4. Counter observation tally sheet (30 minutes)

Print one sheet per session and stand somewhere you can see the counter screen. For every
time someone at the counter **looks up a job**, make one tick in the matching row and column.

Shop: ____________ Date: ________ Start time: ______ Observer: ____________

| How they found the job | Walk-in | Phone call | Tech asked | Total |
|---|---|---|---|---|
| Customer's **name** typed | | | | |
| **Phone number** (full) typed | | | | |
| **Last 4 digits** of phone | | | | |
| **Ticket / WO number** typed | | | | |
| **Scanned** a ticket or label | | | | |
| **Scrolled / browsed** the list (no search) | | | | |
| Paper ticket or whiteboard (no computer) | | | | |
| Asked a tech / shouted to the back | | | | |
| Gave up / couldn't find it | | | | |

Also note:

- Average time from "customer speaks" to "job on screen": _____ s
- Did anyone pull out a phone to look up a job? Who and why: ______________________
- Anything that slowed the counter down: ______________________

Compare your ticks with the report's **counter lookups** for the same 30 minutes.

- The report only sees searches typed into Tekly. Your sheet also catches paper, browsing and
  shouting.
- The gap between the two tells you how much lookup happens **outside** the software.

---

## 5. Pulling the report

**Web app:** go to **Reports → Go pilot**. You only see this tab with "Manage settings".

- Set **From** and **To**. Dates are **UTC days**: a late-evening shift in the Americas may fall
  on the next UTC day, so widen the range by a day if in doubt.
- Read the headline and the per-person table.
- Click **Download CSV** to save it.

**API**, for owners and admins (`can_manage_settings`):

```
GET /api/pilot/go-report?from=2026-10-27&to=2026-10-28
    [&order_ids=<id>,<id>]       # default: every job that got a pilot label
    [&employee_ids=<id>,<id>]    # limits the headline to these people (use the techs)
    [&format=csv]                # default json
```

Example with a bearer token. Sign in, then copy `ros_tok` from the browser's local storage:

```bash
curl -H "Authorization: Bearer $TOKEN" "https://<host>/api/pilot/go-report?from=2026-10-28&to=2026-10-28&employee_ids=<tech1>,<tech2>"
```

### What each number means

Per person, on the **pilot jobs** in the range:

| Column | Meaning |
|---|---|
| Status changes (phone / all) | Status changes (not job creation) made from Tekly Go vs from anywhere |
| Photos (phone / all) | Photos added to the job: Go camera vs web upload, intake and feed photos |
| Messages from Go | Customer messages sent from Go: portal thread, plus SMS/WhatsApp handoffs the person confirmed as sent |
| Scans | Times this person scanned a code in Go (Scan tab, or opening a label link with the camera) |
| Lookups: Name / Phone / Ticket no. / Last 4 / Scanner / Other | Job searches typed in the **web app** (work-orders search box and the ⌘K search), by kind. Not limited to pilot jobs. |

How searches are counted:

- **One burst of typing counts once.** "M", "Ma", "Maria" within 20 s is one Name lookup.
- **"Last 4"** means exactly four digits on their own. A shop whose ticket numbers are four
  digits typed without the prefix will see those counted as "Last 4", so check with the tally
  sheet.

### Applying the decision rule

The headline is `share_status_changes_on_phone` = phone status changes ÷ all status changes,
summed over the people counted.

1. Pull **day 2 only**, with `employee_ids` set to **the techs**. Counter staff marking jobs
   "Collected" at the till shouldn't dilute a question about the bench.
2. Read `headline.verdict`:
   - `scan_and_timer`: the share is ≥ 50%. Techs use phones, so keep the Expo plan centred on
     scan + timer.
   - `counter_and_owner`: the share is < 50%. Re-centre Expo on counter + owner: search,
     check-in and push.
   - `not_enough_data`: no status changes yet. Keep going, or label more active jobs.
3. Sanity checks before you act on it:
   - **Fewer than ~15 status changes in total?** Treat the result as a hint, not a decision.
   - **Did one tech do most of the changes?** Look at the per-person shares. One enthusiast
     isn't a shop.
   - **Read the techs' "what got in the way" answers.** If the reason is fixable (no pocket,
     cases), say so in the write-up. If it's structural (gloves, ESD rules, phones banned on
     the bench), that's the answer.
   - **Counter lookups:** if Name + Phone + Last 4 clearly beat Ticket no. + Scanner, the
     counter finds jobs by person, not ticket. That backs "search first" whatever the tech
     share says.

---

## 6. Privacy notes

- **No search text is stored.** A lookup records who searched, when, which box, the **kind**
  of search (name / phone / ticket / last 4 / email / other) and whether anything matched.
  Scans record the kind of code (Go link, ticket number, part, asset), not the code itself.
- **Labels carry no personal data in the QR.**
  - The QR is a link containing only the job's internal id. Opening it requires a Tekly sign-in
    for that shop. Another shop's staff can't open it.
  - The printed text shows the customer's **first name** and the device, as a paper ticket
    already does. Keep labels on tickets inside the shop. Don't put them on items that leave the
    building before the job is closed.
- **Who can see what.**
  - Only owners and admins ("Manage settings") can pull the report.
  - Labels need "Create tickets", and only for jobs the person can already see.
- **What's recorded for everyone, not just the pilot.**
  - The status-change, customer-message and audit tables now remember which app made each
    change ("go/…" or "web"). This is ordinary operational metadata.
  - Lookups and label prints are written to the audit log, which admins can already see.
- **Tell staff.** Say plainly that the pilot counts what is done on the phone vs the computer,
  per person, to decide what to build. It's about the app, not about their performance. Don't
  use the numbers for anything else.
- **Cleaning up afterwards (optional).** Pilot rows can be deleted from `audit_log` where
  `action IN ('pilot.lookup', 'pilot.labels_printed')`. Photo counts (`order.photos_added`) are
  harmless, and the `client` columns can stay.

---

## Appendix: how it works (for developers)

- **Tekly Go marks its requests** with `X-Tekly-Client: go/<version>`.
  - It sends the header from the shared api client: `setClientTag` in
    `frontend-react/src/api/client.ts`, called from `src/go/main.tsx`.
  - The offline queue (`src/go/lib/offlineQueue.ts`) sends it too.
  - The version is `GO_VERSION` in `src/go/lib/client.ts`.
- **The server records where each change came from.**
  - `backend/core/client_source.py` (pure ASGI middleware) puts the header in a context
    variable.
  - The `client` columns on `order_status_logs`, `order_comments` and `audit_log` default to it.
    Values are `go/<v>`, `web` (any other API request), or NULL (no request).
  - Migration: `gp144g4`.
- **Photos.** Photos added are `audit_log` rows with action `order.photos_added` and diff
  `{count, how}`. They're written from the intake-photo add/replace endpoints, order creation
  and feed photo posts.
- **Lookups** are `audit_log` rows with action `pilot.lookup` and diff
  `{via: search|scan, by, surface, matched}`.
  - Searches are logged from `GET /api/orders?search=|q=` (first page only, typing bursts
    collapsed).
  - Scans are logged from `POST /api/scan/resolve`. It now also resolves `/go/job/<id>` and
    `/orders?open=<id>` links, within the caller's org only.
  - Go's Scan screen opens a job link at once, and still reports it to resolve in the
    background (`src/go/lib/scanRoute.ts`).
  - A label opened from the phone's camera carries `?via=qr`. `noteLabelOpen` reports it once
    and strips the marker.
- **Labels:**
  - `GET /api/pilot/job-labels` (`backend/routers/pilot.py`) needs `can_create_tickets`.
  - Rendering is in `services/pilot.render_job_labels` (reportlab, with a segno QR drawn as
    vectors).
  - Each print writes `pilot.labels_printed` per job, which defines the report's default job set.
- **Report:**
  - `GET /api/pilot/go-report` needs `can_manage_settings`. The logic is in
    `services/pilot.go_report`.
  - The web page is `frontend-react/src/pages/reports/GoPilotReport.tsx`.
- **Tests:** `backend/tests/test_go_pilot.py`, `src/go/lib/client.test.ts`,
  `src/go/lib/scanRoute.test.ts`, `src/pages/orders/JobLabels.test.tsx` and
  `src/pages/reports/GoPilotReport.test.tsx`.
