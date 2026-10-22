// Contacts list: the tier pill is an exception marker, so only real loyalty tiers show one.
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("../../components/Shell", () => ({ default: ({ children }: any) => children, M: ({ n }: { n: string }) => <i data-icon={n} /> }));
import { TierPill } from "./ClientsHub";

describe("TierPill", () => {
  it("shows a pill for VIP and loyalty tiers", () => {
    const { unmount } = render(<TierPill tier="vip" />);
    expect(screen.getByText("VIP")).toBeInTheDocument();
    unmount();
    render(<TierPill tier="gold" />);
    expect(screen.getByText("Gold")).toBeInTheDocument();
  });

  it("shows a quiet dash for standard or no tier", () => {
    for (const t of ["standard", "Standard", "", null, undefined]) {
      const { container, unmount } = render(<TierPill tier={t} />);
      expect(container.textContent).toBe("—");
      expect(screen.queryByText("Standard")).toBeNull();
      unmount();
    }
  });
});
