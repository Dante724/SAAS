"""Onboarding gate: a new shop in each launch country starts with the right currency, time zone
and tax, and a job line charges that tax.

    python tests-e2e/api_gate/countries.py            (TEKLY_URL=... for another server)
    python tests-e2e/api_gate/countries.py CA-AB CA-ZZ   (just these rows)

Nine sign-ups (AU, US, GB, CA with no province, CA-ON, CA-AB, CA with an unknown province, DE,
NZ). Sign-up is rate-limited per IP, so the script waits out a 429 (Retry-After) instead of failing.
The expected values below are the real-world rules, not copied from the code.

Canada: a shop's province decides its tax (HST 13% in Ontario, GST 5% in Alberta, ...). With no
province, or one sign-up doesn't know, the province comes from the time zone, and the Canadian
default time zone America/Toronto means Ontario: HST 13%. Sign-up never fails on a bad province.
"""
import sys

from gate import FAIL, PASS, SKIP, Api, items, log, money, must, parse_only, register_org

# country, province, currency code, time zone, tax name, rate %, mode
# mode: "included" = shelf prices include the tax (AU/GB/DE/NZ), "imposed" = added on top (US/CA).
EXPECTED = [
    ("AU", None, "AUD", "Australia/Sydney", "GST", 10.0, "included"),
    ("US", None, "USD", "America/New_York", "Sales tax", 0.0, "imposed"),   # state rate: owner sets it
    ("GB", None, "GBP", "Europe/London", "VAT", 20.0, "included"),
    ("CA", None, "CAD", "America/Toronto", "HST", 13.0, "imposed"),   # no province: Toronto time = Ontario
    ("CA", "ON", "CAD", "America/Toronto", "HST", 13.0, "imposed"),   # Ontario, explicitly
    ("CA", "AB", "CAD", "America/Toronto", "GST", 5.0, "imposed"),    # Alberta: GST only, no PST
    ("CA", "ZZ", "CAD", "America/Toronto", "HST", 13.0, "imposed"),   # unknown province: sign-up still works,
                                                                      # tax falls back to the time zone (ON)
    ("DE", None, "EUR", "Europe/Berlin", "VAT", 19.0, "included"),
    ("NZ", None, "NZD", "Pacific/Auckland", "GST", 15.0, "included"),
]


def province_supported():
    """Behaviour probe: does sign-up offer provinces at all? (W3-E adds them.)"""
    opts = must(Api().get("/api/auth/signup-options"), "signup options")
    text = str(opts).lower()
    return "province" in text or "subdivision" in text or "'on'" in text


def check_country(country, province, cur, tz, tax_name, rate, mode):
    extra = {"province": province} if province else None
    owner = register_org(f"{country.lower()}{province or ''}", country=country, extra=extra)
    co = must(owner.get("/api/staff/company"), "company")
    problems = []
    if co.get("currency_code") != cur:
        problems.append(f"currency {co.get('currency_code')} (want {cur})")
    if co.get("timezone") != tz:
        problems.append(f"timezone {co.get('timezone')} (want {tz})")
    taxes = items(must(owner.get("/api/catalog/taxes"), "taxes"))
    default = next((t for t in taxes if t.get("is_default")), None)
    if not default:
        problems.append(f"no default tax ({taxes})")
    else:
        if money(default["rate"]) != rate:
            problems.append(f"default tax {default['name']} {default['rate']}% (want {tax_name} {rate}%)")
        if tax_name.lower() not in (default.get("name") or "").lower():
            problems.append(f"tax name {default.get('name')!r} (want {tax_name})")
    client = must(owner.post("/api/clients", {"name": "Country check"}), "client")["id"]
    order = must(owner.post("/api/orders", {"client_id": client, "lines": [
        {"kind": "service", "description": "Diagnosis", "qty": 1, "unit_price": 100}]}), "job")
    line = order["lines"][0]
    if money(line.get("tax_rate")) != rate:
        problems.append(f"job line tax_rate {line.get('tax_rate')} (want {rate})")
    if rate and line.get("tax_mode") != mode:
        problems.append(f"job line tax_mode {line.get('tax_mode')} (want {mode})")
    if order.get("currency_code") != cur:
        problems.append(f"job currency {order.get('currency_code')} (want {cur})")
    summary = f"{co.get('currency_code')} {co.get('timezone')} {default and default['name']} {line.get('tax_rate')}% {line.get('tax_mode')}"
    return problems, summary


def main():
    log("Country onboarding gate")
    only = parse_only(sys.argv)
    rows = [r for r in EXPECTED if not only or (r[0] + (f"-{r[1]}" if r[1] else "")) in only]
    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    for country, province, cur, tz, tax_name, rate, mode in rows:
        label = country + (f"-{province}" if province else "")
        try:
            problems, summary = check_country(country, province, cur, tz, tax_name, rate, mode)
        except Exception as e:  # noqa: BLE001 — one country failing to sign up shouldn't hide the rest
            problems, summary = [f"error: {e}"], ""
        status = PASS if not problems else FAIL
        if status == FAIL and province and not province_supported():
            status = SKIP
            problems = [f"pending W3-E (sign-up has no province yet): {'; '.join(problems)}"]
        counts[status] += 1
        log(f"{status:4}  {label:6} {'; '.join(problems) if problems else summary}")
    log("")
    log(f"SUMMARY {len(rows)} countries: {counts[PASS]} PASS, {counts[FAIL]} FAIL, {counts[SKIP]} SKIP")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
