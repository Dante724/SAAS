import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import Button, { IconButton, buttonClass } from "./Button";
import Tabs from "./Tabs";
import EmptyState, { ErrorState, TableEmpty } from "./EmptyState";
import Drawer from "./Drawer";
import { Toolbar } from "./PageLayout";
import SaveFooter from "./SaveFooter";

describe("Button", () => {
  it("is one look per variant and size, with a 44px touch area class", () => {
    expect(buttonClass("primary", "md")).toContain("h-9");
    expect(buttonClass("primary", "md")).toContain("bg-primary");
    expect(buttonClass("secondary", "lg")).toContain("h-11");
    expect(buttonClass("primary")).toContain("hit44");
  });
  it("is disabled while busy", () => {
    render(<Button variant="primary" busy>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
  it("icon buttons are named by their label", () => {
    render(<IconButton label="Close" icon={<span />} />);
    expect(screen.getByRole("button", { name: "Close" })).toHaveAttribute("title", "Close");
  });
});

describe("Tabs", () => {
  const tabs = [{ id: "a", label: "Overview" }, { id: "b", label: "Payroll", count: 3 }] as const;
  it("selected tab changes colour only, never weight", () => {
    render(<Tabs label="Sections" tabs={[...tabs]} value="a" onChange={() => {}} />);
    const on = screen.getByRole("tab", { name: "Overview" });
    const off = screen.getByRole("tab", { name: /Payroll/ });
    expect(on).toHaveAttribute("aria-selected", "true");
    const weight = (el: HTMLElement) => el.className.split(" ").filter((c) => c.startsWith("font-"));
    expect(weight(on)).toEqual(weight(off));
    expect(on.className).toContain("shrink-0");
  });
  it("chips report pressed state and call onChange", () => {
    const onChange = vi.fn();
    render(<Tabs look="chips" label="Status" tabs={[...tabs]} value="a" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /Payroll/ }));
    expect(onChange).toHaveBeenCalledWith("b");
    expect(screen.getByRole("button", { name: "Overview" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("EmptyState", () => {
  it("shows a title, a line and one action", () => {
    render(<EmptyState title="No tasks yet" body="Tasks you add show here." action={<Button>Add a task</Button>} />);
    expect(screen.getByText("No tasks yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a task" })).toBeInTheDocument();
  });
  it("table version spans every column", () => {
    render(<table><tbody><TableEmpty cols={6} title="Nothing here" /></tbody></table>);
    expect(screen.getByText("Nothing here").closest("td")).toHaveAttribute("colspan", "6");
  });
  it("error state offers a retry", () => {
    const retry = vi.fn();
    render(<ErrorState onRetry={retry} />);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalled();
  });
});

describe("Drawer", () => {
  it("is a named dialog that closes on Escape", () => {
    const onClose = vi.fn();
    render(<Drawer title="Edit client" label="Edit client" onClose={onClose}>Body</Drawer>);
    expect(screen.getByRole("dialog", { name: "Edit client" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("Toolbar", () => {
  it("keeps actions first and pins search and filters to the right", () => {
    const { container } = render(<Toolbar actions={<button>New</button>} search={<input aria-label="Search" />} />);
    const groups = container.firstElementChild!.children;
    expect(groups[0]).toContainElement(screen.getByRole("button", { name: "New" }));
    expect(groups[groups.length - 1].className).toContain("ml-auto");
  });
});

describe("SaveFooter", () => {
  it("puts Save first and in the primary colour", () => {
    render(<SaveFooter dirty onSave={() => {}} onDiscard={() => {}} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons[0]).toHaveTextContent("Save");
    expect(buttons[0].className).toContain("bg-primary");
  });
});
