// A4 — a label template picks the field {Barcode} encodes, and a barcode type per field.
import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LabelBarcodePicker, { type BarcodeOptions, typeFor } from "./LabelBarcodePicker";

function Harness({ start, spy }: { start: BarcodeOptions; spy: (v: BarcodeOptions) => void }) {
  const [v, setV] = useState(start);
  return <LabelBarcodePicker value={v} onChange={(n) => { setV(n); spy(n); }} />;
}

describe("LabelBarcodePicker", () => {
  it("defaults to the legacy first-filled source with no type picker", () => {
    render(<Harness start={{ barcode_field: null, barcode_types: {} }} spy={() => {}} />);
    expect(screen.getByLabelText("{Barcode} encodes")).toHaveValue("");
    expect(screen.queryByLabelText(/Barcode type for/)).toBeNull();
  });

  it("keeps a separate type per field", async () => {
    const u = userEvent.setup();
    const spy = vi.fn();
    render(<Harness start={{ barcode_field: null, barcode_types: {} }} spy={spy} />);
    await u.selectOptions(screen.getByLabelText("{Barcode} encodes"), "mpn");
    await u.selectOptions(screen.getByLabelText("Barcode type for MPN"), "ean13");
    expect(screen.getByText(/check digit is added or verified/)).toBeInTheDocument();
    await u.selectOptions(screen.getByLabelText("{Barcode} encodes"), "sku");
    expect(screen.getByLabelText("Barcode type for SKU")).toHaveValue("code128");
    await u.selectOptions(screen.getByLabelText("Barcode type for SKU"), "qr");
    const last = spy.mock.calls.at(-1)![0] as BarcodeOptions;
    expect(last).toEqual({ barcode_field: "sku", barcode_types: { mpn: "ean13", sku: "qr" } });
    expect(typeFor(last, "mpn")).toBe("ean13");
    expect(typeFor(last, "code")).toBe("code128");
  });
});
