// D3: the Contacts list tag filter chip.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import TagFilterChip from "./TagFilterChip";

const reply = (body: unknown) => ({ ok: true, status: 200, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
afterEach(() => vi.unstubAllGlobals());

function Host({ onPick }: { onPick: (v: string | null) => void }) {
  const [v, setV] = useState<string | null>(null);
  return <TagFilterChip value={v} onChange={(id) => { setV(id); onPick(id); }} />;
}

describe("TagFilterChip", () => {
  it("picks a tag, shows it as a chip, and clears it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ items: [{ id: "t1", name: "Caller", color: "#000" }, { id: "t2", name: "VIP", color: "#fff" }] })));
    const u = userEvent.setup();
    const onPick = vi.fn();
    render(<Host onPick={onPick} />);
    await u.selectOptions(await screen.findByLabelText("Filter by tag"), "t2");
    expect(onPick).toHaveBeenLastCalledWith("t2");
    expect(screen.getByText("VIP")).toBeInTheDocument();
    expect(screen.queryByText(/Tag:/)).toBeNull();
    await u.click(screen.getByRole("button", { name: "Clear tag filter" }));
    expect(onPick).toHaveBeenLastCalledWith(null);
    expect(await screen.findByLabelText("Filter by tag")).toBeInTheDocument();
  });

  it("renders nothing when the shop has no tags", async () => {
    const f = vi.fn(async () => reply({ items: [] }));
    vi.stubGlobal("fetch", f);
    const { container } = render(<Host onPick={() => {}} />);
    await vi.waitFor(() => expect(f).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
