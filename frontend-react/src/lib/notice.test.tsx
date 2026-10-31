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
