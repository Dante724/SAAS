import { describe, it, expect } from "vitest";
import { publicRoute } from "./publicRoutes";

describe("publicRoute", () => {
  it("sends /portal/<token> to the customer portal with its token", () => {
    expect(publicRoute("/portal/abc123")).toEqual({ page: "portal", token: "abc123" });
    expect(publicRoute("/portal/abc123/")).toEqual({ page: "portal", token: "abc123" });
  });

  it("an empty portal token still lands on the portal (which says the link is invalid)", () => {
    expect(publicRoute("/portal/")).toEqual({ page: "portal", token: "" });
  });

  it("keeps the track page and its lookup", () => {
    expect(publicRoute("/track/tok9")).toEqual({ page: "track", token: "tok9" });
    expect(publicRoute("/track/tok9/")).toEqual({ page: "track", token: "tok9" });
    expect(publicRoute("/track")).toEqual({ page: "trackLookup" });
    expect(publicRoute("/track/")).toEqual({ page: "trackLookup" });
  });

  it("keeps booking and auth pages, and everything else is the signed-in app", () => {
    expect(publicRoute("/book/shop")).toEqual({ page: "book" });
    expect(publicRoute("/login")).toEqual({ page: "auth", path: "/login" });
    expect(publicRoute("/accept-invite")).toEqual({ page: "auth", path: "/accept-invite" });
    expect(publicRoute("/")).toEqual({ page: "app" });
    expect(publicRoute("/orders")).toEqual({ page: "app" });
    expect(publicRoute("/portal")).toEqual({ page: "app" });
    expect(publicRoute("/portals/x")).toEqual({ page: "app" });
  });

  it("sends a check-in point's QR and the customer's status page to self check-in", () => {
    expect(publicRoute("/c/k7Q2abc")).toEqual({ page: "checkin", token: "k7Q2abc" });
    expect(publicRoute("/c/s/st9")).toEqual({ page: "checkinStatus", token: "st9" });
    expect(publicRoute("/clients")).toEqual({ page: "app" });
  });
});
