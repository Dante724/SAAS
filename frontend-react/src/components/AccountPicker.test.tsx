// B2: the shared pay-dialog account picker — preselect rule and the inline "Set up a cashbox" form.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

const perms = { deny: new Set<string>() };
vi.mock("../context/Me", () => ({ useMe: () => ({ can: (c?: string) => !c || !perms.deny.has(c), me: null, loading: false }) }));

import AccountPicker, { accountsFor, pickDefaultAccount, type PayAccount } from "./AccountPicker";

const ACCTS: PayAccount[] = [
  { id: "c-other", name: "Uptown till", payment_option: "cash", location_id: "loc2" },
  { id: "c-co", name: "Company cash", payment_option: "cash", location_id: null },
  { id: "c-here", name: "Downtown till", payment_option: "cash", location_id: "loc1" },
  { id: "k1", name: "Card reader", payment_option: "card", location_id: "loc2" },
  { id: "b1", name: "Bank", payment_option: "cashless" },
];

describe("pickDefaultAccount", () => {
  const cash = accountsFor(ACCTS, "cash");
  it("prefers the account at the job's location", () => expect(pickDefaultAccount(cash, "loc1")).toBe("c-here"));
  it("falls back to a company-wide account", () => expect(pickDefaultAccount(cash, "loc9")).toBe("c-co"));
  it("uses the company-wide account when there is no location", () => expect(pickDefaultAccount(cash, null)).toBe("c-co"));
  it("else takes the first", () => expect(pickDefaultAccount(accountsFor(ACCTS, "card"), "loc1")).toBe("k1"));
  it("is empty only when there are no accounts", () => expect(pickDefaultAccount([], "loc1")).toBe(""));
  it("accountsFor can take several options", () => expect(accountsFor(ACCTS, ["card", "cashless"]).map((a) => a.id)).toEqual(["k1", "b1"]));
});

let posts: { url: string; body: any }[] = [];
let postStatus = 201;
const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
beforeEach(() => {
  posts = []; postStatus = 201; perms.deny = new Set();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if ((init?.method || "GET") === "POST") { posts.push({ url, body }); return postStatus < 400 ? reply({ id: "new1", ...body }, postStatus) : reply({ detail: "Forbidden" }, postStatus); }
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

function Harness({ accounts, option, locationId, initial = "" }: { accounts: PayAccount[]; option: "cash" | "card" | "cashless"; locationId?: string | null; initial?: string }) {
  const [list, setList] = useState(accounts);
  const [value, setValue] = useState(initial);
  return <>
    <AccountPicker accounts={list} paymentOption={option} locationId={locationId} value={value} onChange={setValue} onCreated={(a) => setList((l) => [...l, a])} />
    <output data-testid="value">{value}</output>
  </>;
}

describe("AccountPicker", () => {
  it("preselects the account at this location and lists only the method's accounts", async () => {
    render(<Harness accounts={ACCTS} option="cash" locationId="loc1" />);
    await vi.waitFor(() => expect(screen.getByTestId("value").textContent).toBe("c-here"));
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("c-here");
    expect(screen.queryByRole("option", { name: "Card reader" })).toBeNull();
  });

  it("moves off an account that doesn't fit the method", async () => {
    render(<Harness accounts={ACCTS} option="card" initial="c-here" />);
    await vi.waitFor(() => expect(screen.getByTestId("value").textContent).toBe("k1"));
  });

  it("sets up a cashbox inline when there's none for the method, then selects it", async () => {
    const u = userEvent.setup();
    render(<Harness accounts={ACCTS.filter((a) => a.payment_option !== "card")} option="card" locationId="loc1" />);
    const name = screen.getByLabelText("New cashbox name") as HTMLInputElement;
    expect(name.value).toBe("Card");
    await u.click(screen.getByRole("button", { name: "Set up a cashbox" }));
    await vi.waitFor(() => expect(screen.getByTestId("value").textContent).toBe("new1"));
    expect(posts[0].url).toBe("/api/cash/accounts");
    expect(posts[0].body).toMatchObject({ name: "Card", payment_option: "card", location_id: "loc1", is_company: false });
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("new1");
  });

  it("tells staff without the permission who can set one up", () => {
    perms.deny.add("can_manage_accounts");
    render(<Harness accounts={[]} option="cash" />);
    expect(screen.queryByRole("button", { name: "Set up a cashbox" })).toBeNull();
    expect(screen.getByRole("note").textContent).toMatch(/owner or a manager/);
  });

  it("falls back to the same message when the server refuses", async () => {
    const u = userEvent.setup(); postStatus = 403;
    render(<Harness accounts={[]} option="cash" />);
    await u.click(screen.getByRole("button", { name: "Set up a cashbox" }));
    expect((await screen.findByRole("note")).textContent).toMatch(/owner or a manager/);
  });
});
