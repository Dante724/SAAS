import { describe, it, expect } from "vitest";
import { findJobClue, answersClue, statusText } from "./jobClue";

describe("findJobClue", () => {
  it.each([
    ["Chase parts for WO-1042", { kind: "number", term: "1042" }],
    ["chase wo1042", { kind: "number", term: "1042" }],
    ["Ring about #87", { kind: "number", term: "87" }],
    ["update 1042 when the screen lands", { kind: "number", term: "1042" }],
    ["Call 07700 900123 about pickup", { kind: "phone", term: "07700900123" }],
    ["call +44 7700-900-123", { kind: "phone", term: "447700900123" }],
    ["text 555-0199-22", { kind: "phone", term: "555019922" }],
    ["Call Jane about collection", { kind: "name", term: "Jane" }],
    ["Ring Jane Kim back", { kind: "name", term: "Jane Kim" }],
    ["Order Jane's screen", { kind: "name", term: "Jane" }],
  ])("%s", (text, want) => {
    expect(findJobClue(text)).toEqual(want);
  });

  it.each([
    "Call at 3pm",
    "Call back at 10:30",
    "Order 2x screens",
    "Test the new charge port stock",
    "Restock tempered glass",
    "Check 128gb model",
    "Buy 3 cables",
    "Ok",
    "Call Tomorrow",
    "Replace the battery on Friday",
    "Jo",                               // too short for a name
    "",
  ])("no clue in %j", (text) => {
    expect(findJobClue(text)).toBeNull();
  });

  it("a tagged number wins over a name, a phone over a bare number", () => {
    expect(findJobClue("Call Jane about WO-77")).toEqual({ kind: "number", term: "77" });
    expect(findJobClue("Jane 07700 900123")).toEqual({ kind: "phone", term: "07700900123" });
  });
});

describe("answersClue / statusText", () => {
  it("a name clue needs the client's name; numbers and phones trust the server", () => {
    expect(answersClue({ kind: "name", term: "Jane Kim" }, { client_name: "Jane Kim" })).toBe(true);
    expect(answersClue({ kind: "name", term: "Jane" }, { client_name: "Tom Reed" })).toBe(false);
    expect(answersClue({ kind: "number", term: "1042" }, { client_name: "Tom Reed" })).toBe(true);
  });
  it("status keys read as words", () => {
    expect(statusText("awaiting_parts")).toBe("Awaiting parts");
    expect(statusText(null)).toBe("");
  });
});
