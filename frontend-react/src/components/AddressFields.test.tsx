import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import AddressFields, { addressPayload, hasParts, oneLine, type Address } from "./AddressFields";

describe("address helpers", () => {
  it("builds the same one line as the server", () => {
    expect(oneLine({ addr_street: "1 Market St", addr_city: "San Francisco", addr_region: "CA", addr_postcode: "94105", addr_country: "US" }))
      .toBe("1 Market St, San Francisco, CA 94105, US");
    expect(oneLine({ addr_city: "Leeds", addr_postcode: "LS1 4AP" })).toBe("Leeds, LS1 4AP");
  });
  it("sends blanks as null and knows when parts are set", () => {
    expect(addressPayload({ addr_city: "  Paris ", addr_street: "" })).toEqual({ addr_street: null, addr_city: "Paris", addr_region: null, addr_postcode: null, addr_country: null });
    expect(hasParts({})).toBe(false);
    expect(hasParts({ addr_postcode: "1000" })).toBe(true);
  });
});

describe("AddressFields", () => {
  it("shows the old one-line address until the fields are filled", async () => {
    const u = userEvent.setup();
    const seen = vi.fn();
    function Host() {
      const [a, setA] = useState<Address>({});
      return <AddressFields value={a} onChange={(v) => { setA(v); seen(v); }} legacy="12 High Street, Leeds" />;
    }
    render(<Host />);
    expect(screen.getByText("12 High Street, Leeds")).toBeInTheDocument();
    await u.type(screen.getByLabelText("City"), "Leeds");
    await u.selectOptions(screen.getByLabelText("Country"), "GB");
    expect(seen).toHaveBeenLastCalledWith({ addr_city: "Leeds", addr_country: "GB" });
    expect(screen.queryByText("12 High Street, Leeds")).toBeNull();
  });
});
