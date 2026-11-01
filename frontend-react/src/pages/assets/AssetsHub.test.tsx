// Asset list filter: Active (default) / Written off / All, sent to the server as ?state=.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../orders/AssetDrawer", () => ({ default: () => null }));

import AssetsHub from "./AssetsHub";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let urls: string[] = [];

beforeEach(() => {
  urls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    if (url.startsWith("/api/assets")) {
      const off = url.includes("state=written_off");
      return reply({ total: 1, items: [off
        ? { id: "a2", brand: "Bosch", model: "Saw", asset_status: "written_off" }
        : { id: "a1", brand: "Bosch", model: "Drill", asset_status: "in_use" }] });
    }
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("AssetsHub state filter", () => {
  it("shows active assets by default and switches to written off", async () => {
    const u = userEvent.setup();
    render(<MemoryRouter><AssetsHub /></MemoryRouter>);
    expect(await screen.findByText("Bosch Drill")).toBeTruthy();
    expect(urls.find((x) => x.startsWith("/api/assets"))).toContain("state=active");
    expect(screen.getByRole("tab", { name: /^Active/ }).getAttribute("aria-selected")).toBe("true");

    await u.click(screen.getByRole("tab", { name: /^Written off/ }));
    expect(await screen.findByText("Bosch Saw")).toBeTruthy();
    expect(urls.filter((x) => x.startsWith("/api/assets")).pop()).toContain("state=written_off");
    expect(screen.getAllByText(/Written off/).length).toBeGreaterThan(1);   // the filter + the row pill
  });
});
