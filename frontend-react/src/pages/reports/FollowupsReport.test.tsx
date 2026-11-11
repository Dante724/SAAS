// Follow-ups Phase 1: Reports › Follow-ups — money the follow-ups brought back in a period, the
// counts behind it, and each job (a row opens the job).
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import FollowupsReport from "./FollowupsReport";
import { presetRange } from "./datePresets";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const FILLED = {
  recovered_amount: 2415, currency: "GBP", collected_after_reminder: 11, quotes_approved_after_nudge: 7, messages_sent: 42, texts_after_collection: 0,
  items: [
    { order_id: "o1", order_number: "1019", client_name: "Jane Kim", preset_key: "ready_not_collected", amount: 180, outcome: "collected", outcome_at: "2026-11-09T11:00:00Z" },
    { order_id: "o2", order_number: "1031", client_name: "Sam Ortiz", preset_key: "quote_not_answered", amount: 320, outcome: "approved", outcome_at: "2026-11-08T15:00:00Z" },
  ],
};
const EMPTY = { recovered_amount: 0, currency: "GBP", collected_after_reminder: 0, quotes_approved_after_nudge: 0, messages_sent: 0, texts_after_collection: 0, items: [] };

let body: any = FILLED;
let status = 200;
let urls: string[] = [];
beforeEach(() => {
  urls = []; body = FILLED; status = 200;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => { urls.push(url); return status === 200 ? reply(body) : reply({ detail: "Not Found" }, status); }));
});
afterEach(() => vi.unstubAllGlobals());

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.search}</div>; }
const renderIt = () => render(
  <MemoryRouter initialEntries={["/reports"]}>
    <Routes><Route path="*" element={<><FollowupsReport /><Where /></>} /></Routes>
  </MemoryRouter>,
);

describe("FollowupsReport", () => {
  it("asks for this month by default and shows the money recovered and the counts", async () => {
    renderIt();
    expect(screen.getByText("Adding it up…")).toBeInTheDocument();
    const [f, t] = presetRange("This month");
    expect(urls[0]).toBe(`/api/reports/followups?date_from=${f}&date_to=${t}`);
    expect(await screen.findByTestId("recovered")).toHaveTextContent("£2,415");
    expect(screen.getByText("recovered by follow-ups")).toBeInTheDocument();
    const tile = (label: string) => screen.getByText(label).parentElement as HTMLElement;
    expect(tile("Devices collected after a reminder")).toHaveTextContent("11");
    expect(tile("Quotes approved after a nudge")).toHaveTextContent("7");
    expect(tile("Messages sent")).toHaveTextContent("42");
    expect(tile("Texts after collection")).toHaveTextContent("0");
  });

  it("lists each job and a row opens the job", async () => {
    const u = userEvent.setup();
    renderIt();
    const row = (await screen.findByText("Jane Kim")).closest("tr")!;
    expect(within(row).getByText("#1019")).toBeInTheDocument();
    expect(within(row).getByText("Collected")).toBeInTheDocument();
    expect(within(row).getByText("£180")).toBeInTheDocument();
    expect(within(screen.getByText("Sam Ortiz").closest("tr")!).getByText("Quote approved")).toBeInTheDocument();
    await u.click(row);
    expect(screen.getByTestId("where")).toHaveTextContent("/orders?open=o1");
  });

  it("a preset chip changes the period", async () => {
    const u = userEvent.setup();
    renderIt();
    await screen.findByTestId("recovered");
    await u.click(screen.getByRole("button", { name: "Last month" }));
    const [f, t] = presetRange("Last month");
    await vi.waitFor(() => expect(urls.at(-1)).toBe(`/api/reports/followups?date_from=${f}&date_to=${t}`));
  });

  it("an empty period says so plainly", async () => {
    body = EMPTY;
    renderIt();
    expect(await screen.findByText("Nothing recovered in this period yet")).toBeInTheDocument();
    expect(screen.getByTestId("recovered")).toHaveTextContent("£0");
  });

  it("warns when a text went out after collection", async () => {
    body = { ...FILLED, texts_after_collection: 2 };
    renderIt();
    expect(await screen.findByText(/should always be 0/i)).toBeInTheDocument();
  });

  it("a server without the report (404) says it isn't available yet", async () => {
    status = 404;
    renderIt();
    expect(await screen.findByText("The follow-ups report isn’t available yet")).toBeInTheDocument();
  });
});
