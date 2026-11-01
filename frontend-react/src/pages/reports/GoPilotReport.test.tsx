import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import GoPilotReport, { pct, reportQuery, type PilotReport } from "./GoPilotReport";

const get = vi.fn();
const openAuthedDoc = vi.fn();
vi.mock("../../api/client", () => ({
  api: { get: (...a: any[]) => get(...a) },
  openAuthedDoc: (...a: any[]) => openAuthedDoc(...a),
}));
vi.mock("../../lib/dialog", () => ({ alertDialog: vi.fn() }));

const REP: PilotReport = {
  range: { from: "2026-10-20", to: "2026-10-21" },
  jobs: { source: "labelled", count: 10 },
  headline: { status_changes_total: 8, status_changes_go: 3, share_status_changes_on_phone: 0.375,
              photos_total: 4, photos_go: 4, threshold: 0.5, verdict: "counter_and_owner",
              reading: "38% of status changes were made on a phone, below 50%." },
  counter_lookups: { name: 5, phone: 2, wo: 1, last4: 0, scan: 0, other: 0, email: 0, total: 8 },
  employees: [{ employee_id: "e1", name: "Ana", status_changes: { total: 8, go: 3, share_go: 0.375 },
                photos: { total: 4, go: 4 }, messages: { total: 1, go: 1 }, scans: { total: 6, on_pilot_jobs: 5 },
                counter_lookups: { name: 5, phone: 2, wo: 1, last4: 0, scan: 0, other: 0, email: 0, total: 8 } }],
};

beforeEach(() => { get.mockReset(); openAuthedDoc.mockReset(); });

describe("GoPilotReport", () => {
  it("formats shares and builds the query", () => {
    expect(pct(0.375)).toBe("38%");
    expect(pct(null)).toBe("—");
    expect(reportQuery("2026-10-20", "2026-10-21")).toBe("/pilot/go-report?from=2026-10-20&to=2026-10-21");
    expect(reportQuery("2026-10-20", "2026-10-21", "csv")).toBe("/pilot/go-report?from=2026-10-20&to=2026-10-21&format=csv");
  });

  it("shows the headline against the decision line and a row per person", async () => {
    get.mockResolvedValue(REP);
    render(<GoPilotReport />);
    expect(await screen.findByTestId("pilot-share")).toHaveTextContent("38%");
    expect(screen.getByText(/3 of 8 · decision line 50%/)).toBeInTheDocument();
    expect(screen.getByText(/below 50%/)).toBeInTheDocument();
    expect(screen.getByText("Ana")).toBeInTheDocument();
    expect(get.mock.calls[0][0]).toMatch(/^\/pilot\/go-report\?from=\d{4}-\d{2}-\d{2}&to=\d{4}-\d{2}-\d{2}$/);
  });

  it("downloads the CSV for the same range", async () => {
    get.mockResolvedValue(REP);
    openAuthedDoc.mockResolvedValue(undefined);
    render(<GoPilotReport />);
    await screen.findByText("Ana");
    fireEvent.click(screen.getByText("Download CSV"));
    await waitFor(() => expect(openAuthedDoc).toHaveBeenCalledTimes(1));
    expect(openAuthedDoc.mock.calls[0][0]).toMatch(/format=csv$/);
  });

  it("says when the server refuses", async () => {
    get.mockRejectedValue(new Error("Your role does not allow this action (can_manage_settings)"));
    render(<GoPilotReport />);
    expect(await screen.findByText(/does not allow/)).toBeInTheDocument();
  });
});
