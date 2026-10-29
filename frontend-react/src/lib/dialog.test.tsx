import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { selectDialog } from "./dialog";

describe("selectDialog required", () => {
  it("won't confirm without a choice, then resolves to the choice", async () => {
    let result: string | null | undefined;
    await act(async () => { selectDialog("Why?", ["Too expensive"], { required: true, confirmLabel: "Save reason" }).then((v) => { result = v; }); });
    const save = await screen.findByText("Save reason");
    await act(async () => { fireEvent.click(save); });
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose one, or type your own, to continue.");
    expect(result).toBeUndefined();
    await act(async () => { fireEvent.click(screen.getByLabelText("Too expensive")); });
    await act(async () => { fireEvent.click(screen.getByText("Save reason")); });
    await waitFor(() => expect(result).toBe("Too expensive"));
  });

  it("still lets you back out", async () => {
    let result: string | null | undefined;
    await act(async () => { selectDialog("Why?", ["A"], { required: true }).then((v) => { result = v; }); });
    await act(async () => { fireEvent.click(await screen.findByText("Back")); });
    await waitFor(() => expect(result).toBeNull());
  });
});
