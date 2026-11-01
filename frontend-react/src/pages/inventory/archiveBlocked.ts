// QA fix D — what to say when a bulk archive keeps some products back. The server says why per
// item (`blocked_items`); stock is the one reason the user can clear in one step (write it off),
// everything else is listed with its own reason.

export type BlockedItem = { id: string; name: string; reason: string; in_stock?: number };

export function splitBlocked(items: BlockedItem[]): { stocked: BlockedItem[]; other: BlockedItem[] } {
  const stocked = items.filter((i) => (i.in_stock || 0) > 0);
  return { stocked, other: items.filter((i) => !((i.in_stock || 0) > 0)) };
}

export function writeOffPrompt(stocked: BlockedItem[]): string {
  if (stocked.length === 1) {
    const [i] = stocked;
    return `“${i.name}”: ${i.in_stock} still in stock. Write ${i.in_stock === 1 ? "it" : "them"} off and archive?`;
  }
  const list = stocked.map((i) => `${i.name} — ${i.in_stock}`).join("\n");
  return `${stocked.length} products still have stock:\n${list}\n\nWrite it off and archive them?`;
}

export function keptMessage(other: BlockedItem[]): string {
  return `${other.length} item(s) were kept:\n${other.map((i) => `${i.name} — ${i.reason}`).join("\n")}`;
}
