// 16b — the scan box: Enter adds a scan, a pasted list adds many, repeats are skipped, and the
// server's "already in stock" verdict shows on the right entry.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const post = vi.fn();
vi.mock("../api/client", () => ({ api: { post: (...a: any[]) => post(...a) } }));
vi.mock("./Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));

import SerialEntry from "./SerialEntry";
import AssetStateTabs, { readAssetState } from "./AssetStateTabs";

const A = "490154203237518", B = "356789012345011";

function Harness({ target, onState }: { target?: number; onState?: (s: any) => void }) {
  const [v, setV] = useState<string[]>([]);
  return <SerialEntry value={v} onChange={setV} target={target} onState={onState} />;
}

beforeEach(() => {
  post.mockReset();
  post.mockImplementation((_url: string, body: any) => Promise.resolve({
    rows: body.serials.map((s: string) => s.replace(/\D/g, "") === B
      ? { status: "block", message: "Already in stock as iPhone 13." } : { status: "ok" }),
  }));
});

describe("SerialEntry", () => {
  it("adds a scan on Enter and counts toward the target", async () => {
    render(<Harness target={2} />);
    const box = screen.getByLabelText("Scan or type a serial or IMEI");
    await userEvent.type(box, `${A}{Enter}`);
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    expect(box).toHaveValue("");
    expect(box).toHaveFocus();
  });

  it("skips a repeat and shows the server's in-stock verdict", async () => {
    const states: any[] = [];
    render(<Harness target={3} onState={(s) => states.push(s)} />);
    const box = screen.getByLabelText("Scan or type a serial or IMEI");
    await userEvent.type(box, `${A}{Enter}${A}{Enter}`);
    expect(screen.getByText(/already in the list/)).toBeInTheDocument();
    await userEvent.type(box, `${B}{Enter}`);
    expect(screen.getByText("2 of 3")).toBeInTheDocument();
    expect(await screen.findByText(/Already in stock as iPhone 13/)).toBeInTheDocument();
    await waitFor(() => expect(states[states.length - 1]).toMatchObject({ blocked: 1, checking: false }));
    expect(post).toHaveBeenCalledWith("/inventory/serials/check", expect.objectContaining({ serials: [A, B] }));
  });

  it("removes an entry", async () => {
    render(<Harness />);
    const box = screen.getByLabelText("Scan or type a serial or IMEI");
    await userEvent.type(box, `${A}{Enter}`);
    await userEvent.click(screen.getByLabelText(`Remove ${A}`));
    expect(screen.getByText("0 entered")).toBeInTheDocument();
  });
});

describe("SerialEntry when the check fails", () => {
  it("stops saying Checking… and explains, without blocking", async () => {
    post.mockReset();
    post.mockRejectedValue(new Error("offline"));
    const states: any[] = [];
    render(<Harness target={1} onState={(s) => states.push(s)} />);
    await userEvent.type(screen.getByLabelText("Scan or type a serial or IMEI"), `${A}{Enter}`);
    expect(await screen.findByText(/Couldn't check these/)).toBeInTheDocument();
    expect(screen.queryByText("Checking…")).not.toBeInTheDocument();
    await waitFor(() => expect(states[states.length - 1]).toMatchObject({ blocked: 0, checking: false }));
  });
});

describe("AssetStateTabs", () => {
  it("shows counts and defaults to Active", async () => {
    localStorage.removeItem("tekly.assets.state");
    expect(readAssetState()).toBe("active");
    const on = vi.fn();
    render(<AssetStateTabs value="active" onChange={on} counts={{ active: 4, written_off: 2, all: 6 }} />);
    expect(screen.getByRole("tab", { name: /Active\s*4/ })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("tab", { name: /Written off\s*2/ }));
    expect(on).toHaveBeenCalledWith("written_off");
  });
});
