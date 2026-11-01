import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SetupChecklist } from "./SetupChecklist";
import { SETUP } from "./settingsRegistry";

const none = () => Promise.resolve({});
const some = () => Promise.resolve({ shop: true, team: true });
const all = () => Promise.resolve(Object.fromEntries(SETUP.map((x) => [x.id, true])));

beforeEach(() => { try { localStorage.clear(); } catch { /* no storage */ } });

describe("SetupChecklist", () => {
  it("shows every step from real data, none pre-ticked", async () => {
    render(<SetupChecklist goSection={() => {}} load={none} />);
    for (const x of SETUP) expect(await screen.findByText(x.t)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`0 of ${SETUP.length} done`))).toBeInTheDocument();
  });

  it("counts what the shop has actually done and hides finished steps", async () => {
    render(<SetupChecklist goSection={() => {}} load={some} />);
    expect(await screen.findByText(new RegExp(`2 of ${SETUP.length} done`))).toBeInTheDocument();
    expect(screen.queryByText("Add your shop details")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByText("Show 2 finished"));
    expect(screen.getByText("Add your shop details")).toBeInTheDocument();
  });

  it("steps can't be ticked by clicking, but a step that doesn't apply can be skipped", async () => {
    const user = userEvent.setup();
    render(<SetupChecklist goSection={() => {}} load={none} />);
    await screen.findByText("Set up sales tax");
    await user.click(screen.getByText("Set up sales tax"));
    expect(screen.getByText(new RegExp(`0 of ${SETUP.length} done`))).toBeInTheDocument();
    await user.click(screen.getAllByText("Skip")[0]);
    expect(screen.getByText(new RegExp(`1 of ${SETUP.length} done`))).toBeInTheDocument();
  });

  it("Set up opens the right section, and app routes navigate", async () => {
    const user = userEvent.setup();
    const go = vi.fn(); const nav = vi.fn();
    render(<SetupChecklist goSection={go} navigate={nav} load={none} />);
    await user.click(await screen.findByRole("button", { name: "Open: Set up sales tax" }));
    expect(go).toHaveBeenCalledWith("finance");
    await user.click(screen.getByRole("button", { name: "Open: Check in your first repair" }));
    expect(nav).toHaveBeenCalledWith("/orders");
  });

  it("says you're all set when everything is done", async () => {
    render(<SetupChecklist goSection={() => {}} load={all} />);
    expect(await screen.findByText("You're all set")).toBeInTheDocument();
  });
});
