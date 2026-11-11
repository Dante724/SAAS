# Market value of follow-up and task features for small repair/service shops, and limits on automated customer messaging (2023–2026)

Research date: 2026-11-11. Overall caveat: almost every number on approval lift, call reduction or payment speed comes from a vendor that sells the feature. I found no independent or peer-reviewed study for any of them. Each claim below is labelled **[VENDOR]**, **[PRIMARY/LEGAL]**, **[LAW-FIRM COMMENTARY]**, **[USER REVIEW]** or **[PRESS]**.

## Q1. Quote/estimate follow-up: lift from automated reminders and one-click approve links

### Takeaway
Every major field-service and auto-shop platform ships automated quote reminders and online approval. None publishes a controlled approval-rate lift. The figures that exist are vendor marketing, mostly "30% to 45–50% close rate" claims from third-party automation sellers. In auto repair, the best-documented money lever is the digital inspection with photos, sent by text with an approve button. Tekmetric's own data puts it at roughly 21% higher average repair order (ARO).

### Cited Findings
- **Jobber** includes quote follow-ups from the Connect plan up: two automated reminders for quotes in "awaiting response" status, with timing from an hour after the client views the quote to several days later. Jobber publishes no lift figure. **[VENDOR docs]** — [Jobber Help: Automations](https://help.getjobber.com/en/articles/automations/)
- Jobber community users say the built-in reminders are email-only and that texts get more replies. These are anecdotes with no numbers. **[USER FORUM]** — [Jobber Community thread](https://community.getjobber.com/discussions/cleaning-and-property-maintenance/what%E2%80%99s-your-go-to-trick-in-jobber-for-turning-quotes-into-approved-jobs-faster/15606)
- **Housecall Pro**'s "Pipeline" sends automated reminders to customers who haven't approved an estimate. HCP's own guidance suggests a first reminder at 24h and a second at 72h. It publishes no close-rate data. **[VENDOR]** — [Housecall Pro: how to follow up on a quote](https://housecallpro.com/resources/how-to-follow-up-on-quote); [HCP sales pipeline automation](https://www.housecallpro.com/resources/sales-pipeline-automation-for-contractors/)
- A third-party automation vendor claims a 3-touch sequence lifts close rates from about 28–35% to 40–52%. It also cites a "ServiceTitan 2025 benchmark" that top-quartile contractors close 54% of estimates; I could not verify that benchmark. **[VENDOR, competing seller, unverified]** — [US Tech Automations](https://ustechautomations.com/resources/blog/automate-home-services-estimate-follow-up-2026)
- Another vendor claims a 4-touch, 14-day cadence takes close rates from about 30% to 45–50%. **[VENDOR]** — [US Tech Automations](https://ustechautomations.com/resources/blog/home-services-chase-unsigned-estimates-with-timed-followups-recipe-2026)
- ServiceTrade (a commercial field-service vendor) argues that multiple email reminders raise quote approval and suggests up to 15 reminders. It gives no method. **[VENDOR]** — [ServiceTrade blog](https://servicetrade.com/blog/the-key-to-boosting-quote-approvals)
- **Tekmetric** (auto repair) 2024 data: shops using digital vehicle inspections (DVIs) average **$741.60 ARO vs $612.98 without (~21%)**, rising to about $800 with MotoVisuals videos. **[VENDOR aggregate data]** — [Tekmetric: DVI increases ARO](https://www.tekmetric.com/blog-post/digital-vehicle-inspection-software-increase-sales-aro)
- Tekmetric case studies: Branch Automotive ARO $583 → $1,000+ (+77%), attributed to consistent DVI use; Metro Motor about $450 → $800 across 19 shops. **[VENDOR case studies; survivorship bias]** — [Branch Automotive](https://tours.tekmetric.com/answers/how-branch-automotive-increased-its-average-repair-order-by-77-using-digital-vehicle-inspections); [Metro Motor](https://www.tekmetric.com/success-stories/how-metro-motor-standardized-19-shops-and-grew-average-repair-order-to-800)
- A MotoVisuals claim repeated by Tekmetric says close rates rise "from industry average of 55% to over 90%". No data is shown. **[VENDOR partner claim]** — [Tekmetric post](https://www.tekmetric.com/blog-post/digital-vehicle-inspection-software-increase-sales-aro)
- Podium, on payment links rather than quotes: card-not-present phone/mail invoice payments complete "roughly 2 percent" of the time. Beta customers using text-to-pay saw that triple, with 80% less staff time. **[VENDOR, 2020 launch PR]** — [TechCrunch 2020](https://techcrunch.com/2020/03/02/podium-rolls-out-payments-for-its-customer-focused-local-business-saas-service/amp); [American Banker](https://www.americanbanker.com/payments/news/podium-adds-text-message-payments)
- A 2020 study cited by the US Chamber found 35% of customers interested in paying by text, but only 4% of small businesses offered it. **[SECONDARY]** — [US Chamber CO—](https://www.uschamber.com/co/run/finance/text-message-payments/amp)

### Inferences
- Quote reminders plus one-click approve/pay links are **table stakes**: Jobber, HCP, Tekmetric, Podium and RepairDesk all have them. Not having them loses deals; having them is not a differentiator.
- The "rich" approval page (photos, tech notes, line items the customer can approve or decline one by one) is what vendors tie to revenue. A bare "approve" button is not. For Tekly, the approval link should carry the diagnosis photos and notes.
- Sensible defaults from vendor practice: reminder 1 at ~24h, reminder 2 at ~72h, stop after 2–4 touches. Text beats email, according to users.

### Gaps
- No independent A/B or academic data on approval lift from automated reminders or one-click approval in repair or field service.
- Broadly and ServiceTitan publish no verifiable quote-reminder lift numbers that I could find. The ServiceTitan "54%" benchmark is unverified.
- No device-repair-specific (phones/laptops) approval-rate data from RepairDesk, RepairShopr or Orderry.

## Q2. Uncollected/abandoned devices and unpaid balances: rates, reminder effectiveness, notice laws

### Takeaway
There is no published abandonment rate for device repair. Shops handle it with T&Cs (30–60 day pickup windows, storage fees) and reminders. The law sets what an automated "storage/sale notice" must look like: written notice, specific contents, sometimes certified or recorded post, and waiting periods. A text alone is unlikely to satisfy those formal notice rules. Aggressive storage fees carry real legal and reputational risk (the Acme Revival case).

### Cited Findings
- No survey found of how often repair customers never collect. Shop T&Cs show common practice: hold 30 days after notification ([example](https://sourapplerepair.reamaze.com/kb/policies/device-abandonment-policy)) or 60 days ([example](https://mdrepairs.com/terms-of-service)). Storage fees range from $5/day after day 30 to $40/month after day 60. Shops reserve the right to recycle or sell. **[SHOP T&Cs]**
- **Acme Revival (Denver)** filed 85+ lawsuits against customers for "device abandonment" and charged **$25/day after a 15-day grace period**. A customer with $300 binoculars faced a balance near $5,000. On 31 Jan 2025 Jefferson County Judge Keith Goman dismissed 47 cases (jurisdiction) and called the fee "unconscionable". Acme said the fee policy was in its first email and its ToS. **[PRESS]** — [Yahoo/Moneywise, Feb 2025](https://www.yahoo.com/news/colorado-electronics-repair-shop-filed-130200275.html)
- **UK, Torts (Interference with Goods) Act 1977, s.12 + Schedule 1.** Part I is a notice imposing an obligation on the owner to collect. Part II is a notice of intention to sell if goods are not collected by a date. The Part II notice must give the bailee's name and address and enough detail of the goods and where they are held. Para 8 defines the "proper address" for service: last known address, or registered office for a company. s.12(3) covers sale where the owner cannot be traced after reasonable steps. **[PRIMARY/LEGAL + commentary]** — [legislation.gov.uk Schedule 1](https://www.legislation.gov.uk/ukpga/1977/32/schedule/1); [s.12 uncollected goods](https://legislation.gov.uk/ukpga/1977/32/crossheading/uncollected-goods); [Penningtons commentary](https://www.penningtonslaw.com/insights/trash-or-treasure-which-goods-can-a-landlord-throw/); [Markel business hub](https://businesshub.uk.markel.com/link/199fc2b1d496405f8ac0d42b21c5106b.aspx)
- **US state examples** (no federal rule; lien and abandonment law is state by state):
  - **North Carolina** GS 66-67.1: a repair business may dispose of goods unclaimed **60 days after written notice** that they are ready, and must post a sign saying so. — [NCGA statute PDF](https://library.ncleg.net/EnactedLegislation/Statutes/PDF/BySection/Chapter_66/GS_66-67.1.pdf)
  - **New Hampshire** RSA 471-D: non-vehicle property ≤$500 unclaimed 60 days after notice; notice by **certified mail, return receipt requested**. — [NH RSA 471-D](https://gencourt.state.nh.us/rsa/html/XLVI/471-D/471-D-mrg.htm)
  - **Utah** 38-2-3.2 (repairman's lien): **certified mail notice at least 30 days before sale**, stating the property, the claim and the amount needed to redeem. — [Utah Code 38-2](https://le.utah.gov/xcode/Title38/Chapter2/C38-2_1800010118000101.pdf)
  - **Colorado** 38-20-116: notice at least 15 days before disposal, by registered mail; publish in a newspaper if unclaimed. — [FindLaw CO 38-20-116](https://codes.findlaw.com/co/title-38-property-real-and-personal/co-rev-st-sect-38-20-116/)
  - **[PRIMARY/LEGAL; versions vary, so verify current text]**

### Inferences
- Product implication: automated "ready for pickup → reminder → storage-fee warning → final notice" texts and emails are useful **nudges**. The legally effective final notice in most places must be **written and delivered by post**, often certified, registered or recorded. Tekly should generate a printable/PDF statutory notice (pre-filled per country/state template, with goods description, amount owed and sale date) and log the date it was sent. It should not claim an SMS satisfies the law.
- Capture the pickup policy and storage-fee terms at intake, with customer acknowledgement. Acme shows terms buried in an email are weak, and uncapped daily fees can be ruled "unconscionable". Suggest a default fee cap (for example, not more than the device value).
- From memory, not verified this session: a UK Part II notice where money is owed must be sent by post in a registered letter or recorded delivery, with at least 3 months' notice. Listed under Gaps.

### Gaps
- No data on abandonment rates or on how much reminders reduce abandonment or recover unpaid balances in device repair.
- I could not fetch the full Schedule 1 text (legislation.gov.uk blocked automated fetch). Unverified recollection to check before relying on it: (a) a Part II notice must give the bailor a reasonable opportunity to collect; (b) where an amount is payable to the bailee, the notice must be sent by registered letter or recorded delivery and give at least 3 months. Check paras 6–7 directly.
- EU member-state equivalents not researched. California and Texas (big US markets) not checked.

## Q3. Customer status tracking links: do they cut "is it ready?" calls?

### Takeaway
The direction of the effect is widely claimed and plausible. Its size, about 50–75% fewer status calls, comes only from dealership-texting vendor anecdotes. There is no independent study, and Tekmetric and Shopmonkey publish no call-reduction stats that I found.

### Cited Findings
- Numa (dealership AI/texting vendor): status calls are 30–50% of fixed-ops inbound volume. "Seventy-five percent of afternoon inbound calls" are status requests. Structured proactive updates cut status calls 50–75% within 60 days. Unnamed dealers: CDJR −71% in 45 days, Honda −68% in 60 days. **[VENDOR, unnamed customers]** — [Numa: stop status calls](https://numa.com/blog/how-to-stop-customers-calling-for-service-status); [Numa: why departments drown](https://numa.com/blog/why-service-departments-struggle-high-call-volume-status-updates)
- Numa cites a "Customer Service Index" study: customers about 4× more likely to prefer a text status update than a phone call. The original study was not located. **[VENDOR citing unverified study]** — [Numa](https://www.numa.com/blog/automated-status-updates)
- TextBolt claims 20 proactive texts a day replace 30+ inbound calls, with no data. **[VENDOR]** — [TextBolt](https://textbolt.com/blog/send-vehicle-repair-updates/)
- Trade press (Vehicle Service Pros) covers texting adoption in shops and cites a 98–99% SMS open rate. That measures reading, not call reduction. **[TRADE PRESS]** — [VSP: Texting to efficiency](https://www.vehicleservicepros.com/shop-operations/service-repair/article/53070514/texting-to-efficiency); [AutoFlow: how many texts are too much](https://www.vehicleservicepros.com/service-repair/the-garage/blogs/blog/53062378/autoflow-how-many-texts-are-too-much)
- A consultant page claims Tekmetric approval cycles drop from about 6 hours to about 45 minutes with texting. No source. **[THIRD-PARTY, unsourced]** — [ksautomate](https://ksautomate.pages.dev/industries/auto-repair)

### Inferences
- A live status page plus proactive texts at the key moments (checked in, awaiting approval, parts ordered, ready) is the expected baseline. Its value is staff time and phone-free counters, not new revenue. Pitch it to owners as "fewer interruptions".
- The AutoFlow "how many texts are too much" piece points to a real risk of over-messaging. Default to a few high-value status events.

### Gaps
- No Tekmetric, Shopmonkey, RepairDesk or Orderry published call-reduction metric. No independent study, and no Domino's-tracker call-volume data found.

## Q4. SMS consent and compliance: transactional vs marketing (US TCPA/10DLC, UK PECR, EU GDPR)

### Takeaway
A SaaS that sends texts for shops must: (1) keep **transactional/service** and **marketing** streams separate, with separate consent; (2) register 10DLC brand and campaigns in the US, with marketing and informational as separate campaigns; (3) honour STOP-style opt-outs by any reasonable means within 10 business days (US) and immediately in practice; (4) enforce quiet hours (8am–9pm recipient local time is the federal and Florida baseline); (5) keep consent records. In the UK and EU, genuine service messages need no PECR consent, but adding any promotional content turns them into marketing.

### Cited Findings
**US**
- The line between transactional and marketing is about content. Adding a discount or upsell to a service text makes it marketing, which needs **prior express written consent**. Transactional consent does not cover marketing. **[VENDOR/compliance blogs; verify with counsel]** — [Infobip 2026 TCPA guide](https://cdn-web.infobip.com/uploads/markdown-exports/www.infobip.com/blog/tcpa-compliance-sms.md); [SlickText compliance](https://www.slicktext.com/sms-compliance)
- **10DLC** is a carrier requirement separate from TCPA consent. Unregistered traffic is filtered or blocked. Brand approval takes about 1–3 business days and campaigns about 2–7, via The Campaign Registry. **[VENDOR]** — [mytcrplus compliance roadmap](https://mytcrplus.com/navigating-business-texting-the-compliance-roadmap-every-business-sender-needs/)
- Twilio error **30913**: a campaign is rejected when the opt-in combines marketing and informational consent. Marketing consent must be "explicit written consent that is separate from any non-marketing consent" (separate unchecked checkbox, distinct form section, or keyword). Verbal consent is not enough for marketing. The message flow must disclose frequency, "msg & data rates may apply", terms and privacy URL. **[PRIMARY, carrier-aggregator rule]** — [Twilio Error 30913](https://static1.twilio.com/docs/api/errors/30913)
- **FCC revocation rule (effective 11 Apr 2025):** consumers may revoke by any reasonable means. "stop, quit, end, revoke, opt-out, cancel, unsubscribe" are per se valid. Senders must honour revocation within **10 business days**. **[LAW-FIRM COMMENTARY]** — [Covington](https://www.covingtonblogs.com/2024/02/26/fcc-adopts-new-tcpa-consent-revocation-rules/); [BHFS](https://www.bhfs.com/insights/fcc-announces-effective-date-for-new-revocation-of-consent-order)
- The separate "revoke-all" provision (opting out of one type counts for all types, e.g. marketing STOP also stops service texts) was delayed to 11 Apr 2026, then in Jan 2026 delayed again to **31 Jan 2027**. **[LAW-FIRM COMMENTARY]** — [Byte Back (Jul 2026)](https://www.bytebacklaw.com/2026/07/looking-back-on-the-last-year-of-state-level-tcpa-updates/)
- The FCC "one-to-one consent" rule (lead generators) was set for 27 Jan 2025. Sources conflict: one says it is in effect, another that it was cancelled in 2025. Most commentary says the 11th Circuit vacated it in Jan 2025. **Conflicting; verify.** — [Infobip](https://cdn-web.infobip.com/uploads/markdown-exports/www.infobip.com/blog/tcpa-compliance-sms.md); [insiderone](https://insiderone.com/sms-marketing-rules-guide/)
- **State mini-TCPAs:**
  - Florida FTSA (2023 amendment): opt-out by replying "STOP", with a 15-day safe harbor; $500–$1,500 per violation.
  - Florida FCCPA quiet hours of 8am–9pm still apply to phone and text (2025 amendment exempts email only).
  - **Texas SB 140 (June 2025)** brought texts into the Texas Telephone Solicitation Act: registration, $200 fee, $10k security, and a private right of action. A settlement says consent-based texting is exempt from registration.
  - **[LAW-FIRM COMMENTARY]** — [Byte Back](https://www.bytebacklaw.com/2026/07/looking-back-on-the-last-year-of-state-level-tcpa-updates/)

**UK (PECR/ICO)**
- The ICO defines a service message as one sent for administrative or customer-service purposes only. Genuine service messages need no PECR consent. The ICO's "electronic mail" covers SMS, in-app messages and DMs. **[LAW-FIRM COMMENTARY on ICO guidance]** — [Lewis Silkin](https://lewissilkin.com/en/insights/a-service-email-on-your-bike-says-the-ico); [DMA](https://dma.org.uk/article/marketing-emails-the-fine-line-between-a-service-and-sales-message)
- **EE fined** (2021) for texts it considered service messages that contained promotional content. The ICO held the marketing rules applied. **[ENFORCEMENT]** — [Birketts](https://www.birketts.co.uk/legal-update/ee-fined-for-sending-marketing-texts-without-consent); [Edwin Coe](https://www.edwincoe.com/blogs/main/marketing-email-or-service-message-ico-fines-ee-ltd-for-sending-unsolicited-marketing/)
- **Soft opt-in** (marketing own similar products to existing customers) requires a simple chance to refuse at the point of collection and in every message. The ICO rejected soft-opt-in reliance for Royal Mail and Allay. **[LAW-FIRM COMMENTARY]** — [Slaughter and May](https://www.slaughterandmay.com/insights/importedcontent/consents-records-and-disguises-lessons-from-ico-direct-marketing-enforcement-actions/)

### Inferences
What Tekly must build:
- **Two message classes.** "Service" templates (status, approval request, ready, pickup and storage reminder, receipt) are locked against promo content. Review requests, reactivation and discounts are "marketing". Review requests are often treated as marketing; worth a counsel check.
- **Separate, unticked marketing opt-in at intake**, with timestamp, channel, wording version and staff user stored. For the UK, add a soft-opt-in refusal option at intake.
- **STOP/keyword handling** that suppresses at least the class, and plan for revoke-all from 2027. Free-text opt-out detection ("don't text me"). Suppression applied before every send. Audit log.
- **Quiet hours** in the recipient's timezone. Default 8am–9pm for US. Use similar sensible defaults for UK/EU and queue anything that falls outside.
- **10DLC onboarding per shop** (brand plus separate informational and marketing campaigns), or a shared toll-free/short code with verification. Budget days to weeks for approval.
- GDPR/EU: service messages rely on contract or legitimate interest; marketing needs consent (ePrivacy) or soft opt-in where national law allows it. Not deeply researched.

### Gaps
- EU member-state ePrivacy variations not researched. The current FCC one-to-one consent status is conflicting. The exact UK quiet-hours norm (no statutory rule found) is unconfirmed.

## Q5. Adoption: what repair-shop owners pay for and switch for; do task lists, TV wallboards, checklists or AI matter?

### Takeaway
Switching stories among RepairShopr, RepairDesk and Orderry centre on **support responsiveness, modern UI/app, workflow customisation, scheduling, POS and inventory, and integrations (e.g. Xero)**. I found **no evidence** that internal task lists, shop TV wallboards, shift checklists or AI assistants are cited reasons to buy or switch in this segment.

### Cited Findings
- Capterra ratings: RepairDesk 4.7 (244 reviews); RepairShopr 4.4 (128). **[REVIEW SITE]** — [Capterra RepairDesk](https://www.capterra.com/p/146659/RepairDesk/reviews/); [Capterra RepairShopr](https://www.capterra.com/p/133945/RepairShopr/reviews/)
- Switch reasons in Capterra reviews from RepairShopr to RepairDesk: "another shop was using it"; complaints of an outdated app and no clear ability to schedule jobs; RepairShopr customisation not flexible enough. **[USER REVIEW, via search snippets]** — [Capterra RepairDesk](https://www.capterra.com/p/146659/RepairDesk/reviews/); [Capterra RepairShopr](https://www.capterra.com/p/133945/RepairShopr/reviews/)
- RepairDesk-hosted switching stories cite a more modern, customisable POS and repair workflow and stronger support. RepairShopr email replies reportedly took 24+ hours. **[VENDOR-published case study]** — [RepairDesk blog: Spa City iRepair](https://blog.repairdesk.co/2025/04/25/why-spa-city-irepair-switched-from-repairshopr-to-repairdesk/); [RepairDesk: why I moved](https://blog.repairdesk.co/2021/11/01/why-i-moved-my-repair-shop-software-from-repairshopr-to-repairdesk-part-1/)
- **Orderry:** Capterra has 35 reviews (ease of use 4.7, support 4.5); Software Advice 4.7/5 from 36. Praise covers the all-in-one CRM, inventory and invoicing, and the Xero integration. Value for money and support score lowest at 3/5 on Software Advice. SelectHub flags limited offline functionality. No reviews mention SMS. **[REVIEW SITES]** — [Capterra Orderry](https://capterra.com/p/151174/Orderry/reviews/); [Software Advice Orderry](https://www.softwareadvice.com/retail/orderry-profile/reviews/); [SelectHub](https://www.selecthub.com/p/field-service-software/orderry/)
- In auto repair, buy reasons in vendor case studies are DVI, texting and ARO growth (see Q1). **[VENDOR]**

### Inferences
- The things that sell: a fast, modern ticket→approval→pickup flow, customer texts and approvals, POS and inventory, scheduling, reliable support. Internal task managers, wallboards and checklists are retention and "operations polish" features, not purchase drivers, in the evidence found. They are worth building lightly and tied to customer-facing outcomes (e.g. a task auto-created for "quote not approved after 72h" or "device ready 14 days, not collected") rather than as a standalone task app.
- AI assistants are not yet a cited buying reason in this segment's reviews. Position AI as invisible automation (drafting the follow-up, summarising the job) rather than a headline feature.

### Gaps
- The Capterra and G2 review pages returned 403 to direct fetch, so the theme analysis relies on search snippets and vendor case studies, not a systematic review count.
- No survey of repair-shop owners' willingness to pay per feature was found.

## Q6. Pricing signals: do vendors charge extra for automation/marketing SMS?

### Takeaway
Mixed. The common pattern is automation included in plans with **SMS volume metered** (allowances plus credit packs). Marketing-heavy tools such as Podium are separate, expensive subscriptions. Some vendors gate quote follow-ups to higher tiers (Jobber Connect+).

### Cited Findings
- **Jobber** gates automated quote follow-ups to the **Connect plan and up**, not the entry tier. **[VENDOR docs]** — [Jobber Help](https://help.getjobber.com/en/articles/automations/)
- **RepairDesk** markets its email and SMS marketing automation as having "no extra fees" for the tool. A third party (ITQlick) lists SMS credits as a possible hidden cost. G2 lists plans from roughly $99–$149/month. **[VENDOR vs third-party conflict]** — [RepairDesk SMS](https://repairdesk.co/repairdesk-sms); [ITQlick](https://www.itqlick.com/repairdesk/pricing); [G2 RepairDesk pricing](https://www.g2.com/products/repairdesk-repairdesk/pricing)
- **RepairShopr** includes a per-plan SMS allotment ("SMS is a service that we pay for, different plans come with different amounts"), with overage via credit packages. Snail-mail marketing "stamp credits" are bought separately. Plan around $129.99–$139.99/month depending on the snapshot. **[VENDOR pricing page + third party]** — [RepairShopr pricing](https://www.repairshopr.com/pricing); [CostBench](https://costbench.com/software/automotive-shop-software/repairshopr/hidden-costs/)
- **Housecall Pro** Pipeline (estimate follow-up) is sold as a growth feature. One review site describes HCP's price as "drifting" upward. **[THIRD-PARTY]** — [FieldStack review](https://fieldstackpro-site.pages.dev/housecall-pro-review.html)

### Inferences
- A defensible Tekly model: include service and status texts and basic quote and pickup reminders in every plan (table stakes, compliance-safe), meter SMS volume with fair-use allowances plus top-ups (carrier and 10DLC costs are real), and put marketing automation (review requests, reactivation campaigns) on a higher tier. That matches competitor patterns.

### Gaps
- Podium's current list pricing and Orderry's SMS pricing were not confirmed this session. No data on attach rates for SMS add-ons.
