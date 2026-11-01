import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route, Link } from "react-router-dom";
import RouteErrorBoundary from "./RouteErrorBoundary";

function Boom({ msg = "kaboom" }: { msg?: string }): JSX.Element {
  throw new Error(msg);
}

function app(start: string, crash = <Boom />) {
  return (
    <MemoryRouter initialEntries={[start]}>
      <Link to="/fine">to fine</Link>
      <RouteErrorBoundary>
        <Routes>
          <Route path="/broken" element={crash} />
          <Route path="/fine" element={<div>fine page</div>} />
        </Routes>
      </RouteErrorBoundary>
    </MemoryRouter>
  );
}

describe("RouteErrorBoundary", () => {
  it("shows a message instead of a blank app, and keeps the rest of the page", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(app("/broken"));
    expect(screen.getByRole("alert")).toHaveTextContent("This screen hit a problem");
    expect(screen.getByText("kaboom")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go back" })).toBeInTheDocument();
    expect(screen.getByText("to fine")).toBeInTheDocument();
    spy.mockRestore();
  });

  it("clears itself when the user goes to another address", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(app("/broken"));
    fireEvent.click(screen.getByText("to fine"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("fine page")).toBeInTheDocument();
    spy.mockRestore();
  });

  it("asks for a reload when a screen's file is gone after a deploy", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(app("/broken", <Boom msg="Failed to fetch dynamically imported module: /assets/X.js" />));
    expect(screen.getByRole("alert")).toHaveTextContent("A new version of Tekly is available");
    spy.mockRestore();
  });
});
