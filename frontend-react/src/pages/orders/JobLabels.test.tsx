import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor, waitForElementToBeRemoved } from "@testing-library/react";
import { MeProvider } from "../../context/Me";
import { JobLabelsButton, jobLabelsPath, paperFor } from "./JobLabels";

const get = vi.fn();
const openAuthedDoc = vi.fn();
vi.mock("../../api/client", () => ({
  api: { get: (...a: any[]) => get(...a) },
  openAuthedDoc: (...a: any[]) => openAuthedDoc(...a),
}));
vi.mock("./types", () => ({ setCurrencySymbol: () => {} }));
const alertDialog = vi.fn();
vi.mock("../../lib/dialog", () => ({ alertDialog: (...a: any[]) => alertDialog(...a) }));

beforeEach(() => { get.mockReset(); openAuthedDoc.mockReset(); alertDialog.mockReset(); });

describe("job label helpers", () => {
  it("builds the label URL for picked jobs or one person's open jobs", () => {
    expect(jobLabelsPath({ orderIds: ["a", "b"] }, "a4")).toBe("/pilot/job-labels?paper=a4&order_ids=a%2Cb");
    expect(jobLabelsPath({ assignedToId: "e1" }, "letter")).toBe("/pilot/job-labels?paper=letter&assigned_to_id=e1");
  });
  it("picks Letter for US/Canada, A4 elsewhere", () => {
    expect(paperFor("en-US")).toBe("letter");
    expect(paperFor("en-CA")).toBe("letter");
    expect(paperFor("en-GB")).toBe("a4");
    expect(paperFor("de-DE")).toBe("a4");
    expect(paperFor(undefined)).toBe("a4");
  });
});

describe("JobLabelsButton", () => {
  it("opens the label PDF for the picked jobs", async () => {
    get.mockResolvedValue({ is_owner: true, permissions: {} });
    openAuthedDoc.mockResolvedValue(undefined);
    render(<MeProvider><JobLabelsButton pick={{ orderIds: ["o1", "o2"] }} label="QR labels" /></MeProvider>);
    fireEvent.click(await screen.findByText("QR labels"));
    await waitFor(() => expect(openAuthedDoc).toHaveBeenCalledTimes(1));
    expect(openAuthedDoc.mock.calls[0][0]).toMatch(/^\/pilot\/job-labels\?paper=(a4|letter)&order_ids=o1%2Co2$/);
  });
  it("says why when the server refuses", async () => {
    get.mockResolvedValue({ is_owner: true, permissions: {} });
    openAuthedDoc.mockRejectedValue(new Error("Work order not found"));
    render(<MeProvider><JobLabelsButton pick={{ orderIds: ["x"] }} label="QR labels" /></MeProvider>);
    fireEvent.click(await screen.findByText("QR labels"));
    await waitFor(() => expect(alertDialog).toHaveBeenCalledWith("Work order not found"));
  });
  it("is hidden from roles that can't create tickets", async () => {
    get.mockResolvedValue({ is_owner: false, permissions: { can_create_tickets: false } });
    render(<MeProvider><JobLabelsButton pick={{ orderIds: ["o1"] }} label="QR labels" /></MeProvider>);
    await waitForElementToBeRemoved(() => screen.queryByText("QR labels"));
  });
});
