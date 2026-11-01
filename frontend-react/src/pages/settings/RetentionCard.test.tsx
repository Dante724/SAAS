import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RetentionCard from "./RetentionCard";

const Card = ({ title, children }: any) => <section aria-label={title}>{children}</section>;
const DATA = { min_days: 30, max_days: 3650, kinds: [
  { key: "notifications", label: "Sent notifications", days: null },
  { key: "calls", label: "Call records", days: 365 },
] };
let puts: any[] = [];
afterEach(() => vi.unstubAllGlobals());

describe("RetentionCard", () => {
  it("shows the settings, blank meaning keep, and saves days as numbers", async () => {
    puts = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PUT") puts.push(JSON.parse(String(init.body)));
      return { ok: true, status: 200, headers: new Headers(), json: async () => DATA, text: async () => "" } as any;
    }));
    const u = userEvent.setup();
    render(<RetentionCard Card={Card} />);
    const notif = await screen.findByLabelText("Sent notifications");
    expect((notif as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Call records") as HTMLInputElement).value).toBe("365");
    await u.type(notif, "90");
    await u.click(screen.getByRole("button", { name: "Save" }));
    expect(puts).toEqual([{ days: { notifications: 90, calls: 365 } }]);
    expect(await screen.findByText("Saved")).toBeTruthy();
  });
});
