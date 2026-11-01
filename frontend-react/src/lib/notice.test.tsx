import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import { ApiError } from "../api/client";
import { _resetNotices, loadFailed, saveFailed, showNotice } from "./notice";

afterEach(() => { act(() => _resetNotices()); vi.useRealTimers(); });

describe("notice", () => {
  it("turns a failed load into a visible sentence with the server's reason", () => {
    act(() => loadFailed("locations")(new ApiError(500, '{"detail":"Database is busy"}')));
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load locations. Database is busy");
  });

  it("names the action for a failed save", () => {
    act(() => saveFailed("delete the list")(new Error("")));
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't delete the list.");
  });

  it("doesn't stack the same sentence and goes away on its own", () => {
    vi.useFakeTimers();
    act(() => { showNotice("Couldn't load suppliers."); showNotice("Couldn't load suppliers."); });
    expect(screen.getAllByText("Couldn't load suppliers.")).toHaveLength(1);
    act(() => { vi.advanceTimersByTime(8000); });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("can be dismissed", () => {
    act(() => showNotice("Couldn't load tags."));
    act(() => screen.getByRole("button", { name: "Dismiss" }).click());
    expect(screen.queryByText("Couldn't load tags.")).toBeNull();
  });
});

describe("notice text", () => {
  it("skips 401/403 loads, hides HTML bodies and caps long reasons", async () => {
    const { loadFailed } = await import("./notice");
    const host = () => document.querySelector("[data-notice-host]")?.textContent || "";
    loadFailed("cash accounts")(Object.assign(new Error("Forbidden"), { status: 403 }));
    loadFailed("orders")(Object.assign(new Error("Not authenticated"), { status: 401 }));
    expect(host()).not.toContain("cash accounts");
    expect(host()).not.toContain("orders");
    loadFailed("parts")(new Error("<html><body>502 Bad Gateway</body></html>"));
    expect(host()).toContain("Couldn't load parts.");
    expect(host()).not.toContain("<html>");
    loadFailed("clients")(new Error("x".repeat(500)));
    expect(host()).toContain("x".repeat(200) + "…");
    expect(host()).not.toContain("x".repeat(201));
  });
});
