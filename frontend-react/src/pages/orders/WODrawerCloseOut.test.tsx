// B11: the drawer can always meet a status gate. A status that needs a comment asks for it right
// under the header and sends it with the change; finishing a job that still owes money says so
// plainly, and a manager can move it on unpaid on purpose.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import WODrawer from "./WODrawer";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const STATUSES = [
  { id: "s1", key: "in_repair", name: "In repair", status_group: "in_progress", sort_order: 0 },
  { id: "s2", key: "quoted", name: "Quoted", status_group: "in_progress", sort_order: 1, require_comment: true },
  { id: "s3", key: "delivered", name: "Delivered", status_group: "closed", sort_order: 2, is_terminal: true },
];
let patches: any[] = [];

function open() {
  patches = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "PATCH" && url.endsWith("/status")) {
      const b = JSON.parse(String(init.body));
      patches.push(b);
      if (b.status === "delivered" && !b.allow_unpaid) {
        return reply({ detail: { code: "unpaid", message: "This job still has 4,500.00 to pay. Take the payment first, or close it unpaid if the customer will pay later.", owed: 4500, can_override: true } }, 409);
      }
      return reply({ ok: true, status: b.status });
    }
    if (url.includes("/statuses")) return reply({ items: STATUSES });
    if (/\/orders\/o1$/.test(url)) return reply({ id: "o1", order_number: "WO-00001", status: "in_repair", lines: [], comments: [] });
    return reply({ items: [] });
  }));
  render(<MemoryRouter><WODrawer order={{ id: "o1", order_number: "WO-00001", status: "in_repair" } as any} onClose={() => {}} /></MemoryRouter>);
}
async function pick(name: string) {
  fireEvent.click(await screen.findByRole("button", { name: /^In repair/ }));
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(`^${name}`) }));
}

afterEach(() => vi.unstubAllGlobals());

describe("WODrawer close-out gates", () => {
  it("asks for the comment a status needs, then sends it with the change", async () => {
    open();
    await pick("Quoted");
    const box = await screen.findByRole("region", { name: "Comment needed" });
    expect(patches).toEqual([]);   // nothing sent until the comment is written
    fireEvent.change(within(box).getByRole("textbox", { name: "Comment" }), { target: { value: "Customer approved 4,500" } });
    fireEvent.click(within(box).getByRole("button", { name: /Add comment and move to Quoted/ }));
    await vi.waitFor(() => expect(patches).toContainEqual(expect.objectContaining({ status: "quoted", comment: "Customer approved 4,500" })));
    await vi.waitFor(() => expect(screen.queryByRole("region", { name: "Comment needed" })).toBeNull());
  });

  it("says what's owed when finishing an unpaid job, and lets a manager move it on unpaid", async () => {
    open();
    await pick("Delivered");
    const panel = await screen.findByRole("region", { name: "Still to pay" });
    expect(within(panel).getByText(/still has 4,500.00 to pay/)).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "Move to Delivered unpaid" }));
    await vi.waitFor(() => expect(patches.at(-1)).toMatchObject({ status: "delivered", allow_unpaid: true }));
    await vi.waitFor(() => expect(screen.queryByRole("region", { name: "Still to pay" })).toBeNull());
  });
});
