// QA F2: one IMEI / serial per unit that moves; the field asks for the units in the right state.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

vi.mock("./Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));
import UnitSerials, { unnamedUnits } from "./UnitSerials";

const urls: string[] = [];
beforeEach(() => {
  urls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    return { ok: true, status: 200, headers: new Headers(), json: async () => ({ items: [{ serial_number: "AAA", status: "sold" }] }), text: async () => "" } as Response;
  }));
});
afterEach(() => vi.unstubAllGlobals());

function Harness({ need }: { need: number }) {
  const [v, setV] = useState<string[]>([]);
  return <><UnitSerials partId="ph" need={need} value={v} onChange={setV} label="Phone" status="sold" /><output>{v.join(",")}</output></>;
}

describe("UnitSerials", () => {
  it("takes one serial per unit, no repeats, then hides the box", async () => {
    const u = userEvent.setup();
    render(<Harness need={2} />);
    const box = screen.getByLabelText("IMEI or serial for Phone");
    await u.type(box, "AAA{Enter}AAA{Enter}BBB{Enter}");
    expect(screen.getByRole("status")).toHaveTextContent("AAA,BBB");
    expect(screen.queryByLabelText("IMEI or serial for Phone")).toBeNull();
    await u.click(screen.getByRole("button", { name: "Remove AAA" }));
    expect(screen.getByRole("status")).toHaveTextContent("BBB");
    expect(urls.some((x) => x.includes("part_id=ph") && x.includes("status=sold"))).toBe(true);
  });

  it("unnamedUnits lists serialized lines short of a serial", () => {
    const lines = [{ qty: 2, serialized: true, serials: ["A"] }, { qty: 1, serialized: true, serials: ["B"] }, { qty: 3 }];
    expect(unnamedUnits(lines)).toEqual([lines[0]]);
  });
});
