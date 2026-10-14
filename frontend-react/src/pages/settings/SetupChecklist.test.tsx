import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SetupChecklist } from "./SetupChecklist";
import { SETUP } from "./settingsRegistry";

describe("SetupChecklist", () => {
  it("renders every setup item", () => {
    render(<SetupChecklist goSection={() => {}} />);
    for (const x of SETUP) expect(screen.getByText(x.t)).toBeInTheDocument();
  });

  it("starts with 2 of N done", () => {
    render(<SetupChecklist goSection={() => {}} />);
    expect(screen.getByText(`2 of ${SETUP.length} done · a few minutes to go`)).toBeInTheDocument();
  });

  it("toggling a to-do item updates the progress count", async () => {
    const user = userEvent.setup();
    render(<SetupChecklist goSection={() => {}} />);
    // "Set up your repair statuses" starts not done → clicking it makes 3 done
    await user.click(screen.getByRole("button", { name: "Set up your repair statuses" }));
    expect(screen.getByText(`3 of ${SETUP.length} done · a few minutes to go`)).toBeInTheDocument();
  });

  it("clicking Open → calls goSection with the section id and does not toggle the row", async () => {
    const user = userEvent.setup();
    const go = vi.fn();
    render(<SetupChecklist goSection={go} />);
    const opens = screen.getAllByText("Open →");
    await user.click(opens[2]); // the statuses row
    expect(go).toHaveBeenCalledWith("statuses");
    // propagation stopped → progress unchanged (still 2 done)
    expect(screen.getByText(`2 of ${SETUP.length} done · a few minutes to go`)).toBeInTheDocument();
  });
});
