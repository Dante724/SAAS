// Build a spreadsheet (xlsx/csv) from an array-of-arrays and trigger a download. Every browser
// export goes through here, so cells that look like a formula (=, +, -, @) are made plain text
// (lib/sheet guardCell) and can't run in Excel. The writer is lazy-loaded so it never enters
// the main bundle.
export async function downloadSheet(aoa: (string | number)[][], fmt: "xlsx" | "csv", name: string) {
  const { toCsv, toXlsx } = await import("./sheet");
  const blob = fmt === "csv"
    ? new Blob(["﻿" + toCsv(aoa)], { type: "text/csv;charset=utf-8" })   // BOM: Excel reads UTF-8
    : new Blob([toXlsx(aoa) as BlobPart], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `${name}.${fmt}`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}
