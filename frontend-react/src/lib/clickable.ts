import type { KeyboardEvent, SyntheticEvent } from "react";

// Keyboard access for things that aren't <button>s: a card, a list row, a table row.
//   <div {...clickable(() => open(x))}>        → role=button, Tab-focusable, Enter/Space activate
//   <tr {...clickableRow(() => open(x))}>      → keeps the row's table semantics, Tab + Enter
// A key pressed on a control inside (a checkbox, a menu button) is that control's, not the row's.

type Handler = ((e: SyntheticEvent<any>) => void) | undefined | null | false;

function keyHandler(fn: (e: SyntheticEvent<any>) => void, space: boolean) {
  return (e: KeyboardEvent<any>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || (space && e.key === " ")) { e.preventDefault(); fn(e); }
  };
}

export function clickable(fn: Handler) {
  if (!fn) return {};
  return { role: "button" as const, tabIndex: 0, onClick: fn, onKeyDown: keyHandler(fn, true) };
}

export function clickableRow(fn: Handler) {
  if (!fn) return {};
  return { tabIndex: 0, onClick: fn, onKeyDown: keyHandler(fn, false) };
}
