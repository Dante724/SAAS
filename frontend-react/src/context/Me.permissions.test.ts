// The deny-wins / default-allow / owner rule is the heart of the Settings
// regression class (a wrong is_owner check hid 16 sections). These lock it down.
import { describe, it, expect } from "vitest";
import { canPerm, type Me } from "./Me";

const owner: Me = { is_owner: true, permissions: {} };
const admin: Me = { is_owner: false, permissions: { can_manage_settings: true } };
const limited: Me = { is_owner: false, permissions: { can_view_cashflow: false } };

describe("canPerm", () => {
  it("no code → always allowed (the control has no gate)", () => {
    expect(canPerm(owner)).toBe(true);
    expect(canPerm(limited, undefined)).toBe(true);
    expect(canPerm(null)).toBe(true);
  });

  it("before boot (me is null) → allowed, so nothing flashes hidden", () => {
    expect(canPerm(null, "can_view_cashflow")).toBe(true);
    expect(canPerm(null, "can_manage_settings")).toBe(true);
  });

  it("owner is always allowed, even for a perm explicitly false", () => {
    expect(canPerm(owner, "can_view_cashflow")).toBe(true);
    expect(canPerm({ is_owner: true, permissions: { can_view_cashflow: false } }, "can_view_cashflow")).toBe(true);
  });

  it("default-allow: a non-owner without the key still gets it (deny must be explicit)", () => {
    // This is exactly the bug that hid Settings sections: a missing/true perm
    // must NOT be treated as denied for a non-owner admin.
    expect(canPerm(admin, "can_view_cashflow")).toBe(true);
    expect(canPerm(admin, "some_unknown_perm")).toBe(true);
  });

  it("deny-wins: only an explicit false denies a non-owner", () => {
    expect(canPerm(limited, "can_view_cashflow")).toBe(false);
    expect(canPerm(limited, "can_manage_settings")).toBe(true); // not denied
  });

  it("permissions object absent → non-owner still default-allowed", () => {
    expect(canPerm({ is_owner: false }, "can_manage_settings")).toBe(true);
  });
});
