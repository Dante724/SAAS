import { afterEach, describe, expect, it, vi } from "vitest";

const init = vi.fn();
const captureException = vi.fn();
vi.mock("@sentry/react", () => ({ init, captureException }));

import { initSentry, reportError, scrubBreadcrumb, scrubEvent, _resetForTests } from "./sentry";

afterEach(() => { init.mockClear(); captureException.mockClear(); _resetForTests(); });

describe("initSentry", () => {
  it("is a no-op when VITE_SENTRY_DSN is unset", async () => {
    expect(await initSentry("")).toBe(false);
    expect(init).not.toHaveBeenCalled();
    reportError(new Error("x"));
    expect(captureException).not.toHaveBeenCalled();
  });

  it("initialises once, without PII, when a DSN is set", async () => {
    expect(await initSentry("https://k@o1.ingest.sentry.io/1")).toBe(true);
    await initSentry("https://k@o1.ingest.sentry.io/1");
    expect(init).toHaveBeenCalledTimes(1);
    const opts = init.mock.calls[0][0];
    expect(opts.dsn).toBe("https://k@o1.ingest.sentry.io/1");
    expect(opts.sendDefaultPii).toBe(false);
    expect(opts.beforeSend).toBe(scrubEvent);
    expect(opts.beforeBreadcrumb).toBe(scrubBreadcrumb);
    reportError(new Error("boom"));
    expect(captureException).toHaveBeenCalledTimes(1);
  });
});

describe("scrubEvent", () => {
  it("drops request bodies, cookies, headers and query strings, and keeps only the user id", () => {
    const e: any = scrubEvent({
      request: { url: "https://app.tekly.app/orders?email=a@b.co", data: { password: "x" }, cookies: { s: "1" },
                 headers: { Authorization: "Bearer t" }, query_string: "email=a@b.co" },
      user: { id: "u1", email: "asha@example.com", ip_address: "1.2.3.4", username: "asha" },
    } as any);
    expect(e.request.data).toBeUndefined();
    expect(e.request.cookies).toBeUndefined();
    expect(e.request.headers).toBeUndefined();
    expect(e.request.query_string).toBeUndefined();
    expect(e.request.url).toBe("https://app.tekly.app/orders");
    expect(e.user).toEqual({ id: "u1" });
  });

  it("masks email addresses in messages, exceptions and breadcrumbs", () => {
    const e: any = scrubEvent({
      message: "no client asha@example.com",
      exception: { values: [{ type: "Error", value: "Invite for bob.smith+x@shop.co.uk failed" }] },
      breadcrumbs: [{ message: "typed jo@x.io", data: { url: "/api/clients?q=jo@x.io" } }],
    } as any);
    expect(e.message).toBe("no client [email]");
    expect(e.exception.values[0].value).toBe("Invite for [email] failed");
    expect(e.breadcrumbs[0].message).toBe("typed [email]");
    expect(e.breadcrumbs[0].data.url).toBe("/api/clients");
  });

  it("strips link tokens from the page URL and from navigation/fetch breadcrumbs", () => {
    const tok = "Xk3_9aQ-LmZt72hPq0sWv4";
    const e: any = scrubEvent({
      request: { url: `https://app.tekly.app/portal/${tok}` },
      breadcrumbs: [{ category: "navigation", data: { from: "/login", to: `/reset-password?token=${tok}` } },
                    { category: "fetch", data: { url: `/api/public/track/${tok}/approve`, method: "POST" } }],
    } as any);
    expect(e.request.url).toBe("https://app.tekly.app/portal/[token]");
    expect(e.breadcrumbs[0].data.to).toBe("/reset-password");
    expect(e.breadcrumbs[1].data.url).toBe("/api/public/track/[token]/approve");
    expect(JSON.stringify(e)).not.toContain(tok);
    const c: any = scrubBreadcrumb({ category: "fetch", data: { url: `/api/public/portal/${tok}#x` } });
    expect(c.data.url).toBe("/api/public/portal/[token]");
    expect(scrubBreadcrumb({ category: "navigation", data: { to: "/orders/42?tab=parts" } }).data!.to).toBe("/orders/42");
  });
});
