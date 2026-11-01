import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AutoAttachPicker, ChecklistEditor, toBody, toSteps } from "./ChecklistSettings";

// The editor folds sections into steps and shows only some per-check settings. Saving must not
// lose the rest (upsell rules, descriptions, notes) — the old editor dropped them.
describe("checklist editor round trip", () => {
  const tpl = {
    id: "t1", name: "Intake", scope: "all", type_ids: [],
    steps: [{
      title: "Condition", description: "Look it over",
      sections: [
        { title: "A", allow_notes: true, items: [{ label: "Battery", field_kind: "check_result", required: true, client_visible: true, affects_result: true, options: [],
          description: "Under 80% health fails", upsell_kind: "product", upsell_ref_id: "p1", upsell_price: 49, upsell_threshold: null, upsell_comparator: "fail" }] },
        { title: "B", allow_notes: false, items: [{ label: "Screen", field_kind: "dropdown", required: false, client_visible: false, affects_result: false, options: ["Perfect", "Cracked"] }] },
      ],
    }],
  };

  it("keeps hidden settings and folds sections into one step", () => {
    const body = toBody("Intake", "all", [], toSteps(tpl));
    expect(body.steps).toHaveLength(1);
    const sec = body.steps[0].sections[0];
    expect(body.steps[0].description).toBe("Look it over");
    expect(sec.allow_notes).toBe(true);
    expect(sec.items.map((i: any) => i.label)).toEqual(["Battery", "Screen"]);
    expect(sec.items[0]).toMatchObject({ description: "Under 80% health fails", upsell_kind: "product", upsell_ref_id: "p1", upsell_price: 49, upsell_comparator: "fail", client_visible: true });
    expect(sec.items[1]).toMatchObject({ field_kind: "dropdown", options: ["Perfect", "Cracked"], required: false });
  });

  it("only sends choices for pick-one / pick-several", () => {
    const steps = toSteps(tpl);
    steps[0].items[1].field_kind = "text";
    expect(toBody("x", "selected", ["a"], steps).steps[0].sections[0].items[1].options).toEqual([]);
    expect(toBody("x", "selected", ["a"], steps).type_ids).toEqual(["a"]);
  });
});

// E4 — "Attach automatically when the order enters…"

describe("auto-attach statuses", () => {
  const statuses = {
    work_order: [{ key: "in_repair", name: "In repair", color: "#9b7bf4" }, { key: "ready", name: "Ready", color: "#2cd4c0" }],
    estimate: [{ key: "est_progress", name: "In progress", color: "#9b7bf4" }],
  };

  it("only sends auto_status_keys when the editor set them", () => {
    expect(toBody("x", "all", [], [])).not.toHaveProperty("auto_status_keys");
    expect(toBody("x", "all", [], [], [])).toMatchObject({ auto_status_keys: [] });
    expect(toBody("x", "all", [], [], [{ record_type: "estimate", key: "est_progress" }]).auto_status_keys).toEqual([{ record_type: "estimate", key: "est_progress" }]);
  });

  it("toggles statuses per record type", async () => {
    const u = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(<AutoAttachPicker value={[]} onChange={onChange} statuses={statuses} />);
    expect(screen.getByText(/Off\. Pick statuses/)).toBeInTheDocument();
    // "In progress" exists only for estimates; "In repair" only for work orders
    await u.click(screen.getByRole("button", { name: "In repair" }));
    expect(onChange).toHaveBeenLastCalledWith([{ record_type: "work_order", key: "in_repair" }]);
    await u.click(screen.getByRole("button", { name: "In progress" }));
    expect(onChange).toHaveBeenLastCalledWith([{ record_type: "estimate", key: "est_progress" }]);

    rerender(<AutoAttachPicker value={[{ record_type: "work_order", key: "in_repair" }]} onChange={onChange} statuses={statuses} />);
    expect(screen.getByRole("button", { name: "In repair" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Added to the job once/)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "In repair" }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  describe("in the editor", () => {
    let bodies: any[] = [];
    const reply = (body: unknown) => ({ ok: true, status: 200, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
    beforeEach(() => {
      bodies = [];
      vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "PUT" || init?.method === "POST") { bodies.push(JSON.parse(String(init.body))); return reply({}); }
        if (url.includes("record_type=work_order")) return reply({ items: statuses.work_order });
        if (url.includes("record_type=estimate")) return reply({ items: statuses.estimate });
        return reply({ items: [] });
      }));
    });
    afterEach(() => vi.unstubAllGlobals());

    it("loads the saved statuses and saves the change", async () => {
      const u = userEvent.setup();
      const saved = vi.fn();
      const tpl = { id: "t1", name: "Intake", scope: "all", type_ids: [], steps: [{ title: "Condition", sections: [{ title: "", items: [{ label: "Battery", field_kind: "check_result", required: true, options: [] }] }] }] };
      render(<ChecklistEditor tpl={{ ...tpl, auto_status_keys: [{ record_type: "work_order", key: "ready" }] }} onClose={() => {}} onSaved={saved} />);
      expect(await screen.findByRole("button", { name: "Ready" })).toHaveAttribute("aria-pressed", "true");
      await u.click(screen.getByRole("button", { name: "In repair" }));
      await u.click(screen.getByRole("button", { name: "Save" }));
      await vi.waitFor(() => expect(saved).toHaveBeenCalled());
      expect(bodies[0].auto_status_keys).toEqual([{ record_type: "work_order", key: "ready" }, { record_type: "work_order", key: "in_repair" }]);
    });
  });
});
