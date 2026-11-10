import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import TeklySelect from "./TeklySelect";

const hours = Array.from({ length: 24 }, (_, h) => h);

describe("TeklySelect (the Tekly dropdown panel)", () => {
  it("shows the chosen option, opens a panel and picks with a click", () => {
    const onChange = vi.fn();
    render(<TeklySelect forcePanel aria-label="Send at" value="7" onChange={onChange}>
      {hours.slice(0, 5).map((h) => <option key={h} value={h}>{h} AM</option>)}
      <option value="7">7 AM</option>
    </TeklySelect>);
    const btn = screen.getByRole("combobox", { name: "Send at" });
    expect(btn).toHaveTextContent("7 AM");
    fireEvent.click(btn);
    expect(screen.getByRole("option", { name: /7 AM/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("option", { name: /3 AM/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ target: { value: "3" } }));
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("adds a search box to long lists and keeps group headings", () => {
    render(<TeklySelect forcePanel aria-label="Time zone" value="Asia/Kolkata" onChange={() => {}}>
      <optgroup label="Asia">{["Asia/Kolkata", "Asia/Dubai", "Asia/Tokyo"].map((z) => <option key={z} value={z}>{z}</option>)}</optgroup>
      <optgroup label="Europe">{Array.from({ length: 12 }, (_, i) => `Europe/City${i}`).map((z) => <option key={z} value={z}>{z}</option>)}</optgroup>
    </TeklySelect>);
    fireEvent.click(screen.getByRole("combobox", { name: "Time zone" }));
    expect(screen.getByText("Asia")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Search the list" }), { target: { value: "dub" } });
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("option", { name: /Asia\/Dubai/ })).toBeInTheDocument();
  });

  it("works from the keyboard and skips disabled options", () => {
    const onChange = vi.fn();
    render(<TeklySelect forcePanel aria-label="Priority" value="0" onChange={onChange}>
      <option value="0">Normal</option><option value="1" disabled>High</option><option value="2">Urgent</option>
    </TeklySelect>);
    const btn = screen.getByRole("combobox", { name: "Priority" });
    fireEvent.keyDown(btn, { key: "ArrowDown" });
    fireEvent.keyDown(btn, { key: "ArrowDown" });
    fireEvent.keyDown(btn, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ target: { value: "2" } }));
  });

  it("does nothing when disabled", () => {
    render(<TeklySelect forcePanel aria-label="Locked" value="a" disabled onChange={() => {}}><option value="a">A</option></TeklySelect>);
    fireEvent.click(screen.getByRole("combobox", { name: "Locked" }));
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});
