// Polling stops while the tab is hidden and catches up once when it's shown again.
import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useVisibleInterval } from "./useVisibleInterval";

let state: DocumentVisibilityState = "visible";
const setVis = (v: DocumentVisibilityState) => { state = v; document.dispatchEvent(new Event("visibilitychange")); };

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); state = "visible"; });

describe("useVisibleInterval", () => {
  it("pauses while hidden and runs once on return", () => {
    vi.useFakeTimers();
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => state);
    const fn = vi.fn();
    const { unmount } = renderHook(() => useVisibleInterval(fn, 1000));
    expect(fn).toHaveBeenCalledTimes(1);            // immediately
    vi.advanceTimersByTime(2000);
    expect(fn).toHaveBeenCalledTimes(3);
    setVis("hidden");
    vi.advanceTimersByTime(10000);
    expect(fn).toHaveBeenCalledTimes(3);            // nothing while hidden
    setVis("visible");
    expect(fn).toHaveBeenCalledTimes(4);            // catches up at once
    vi.advanceTimersByTime(1000);
    expect(fn).toHaveBeenCalledTimes(5);
    unmount();
    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(5);            // cleaned up
  });
});
