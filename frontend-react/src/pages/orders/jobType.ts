// What the drawer's "Job type" field shows. It used to read "Hardware Repair" on every job, so a
// warranty job looked like paid work; now it is the job's own type, or says plainly that none is set.
export type WOTypeRef = { id: string; name: string };

export function jobTypeLabel(d: { is_estimate?: boolean | null; type_id?: string | null }, types: WOTypeRef[] | null): string {
  if (d.is_estimate) return "Estimate";
  if (!d.type_id) return "No type set";
  if (types === null) return "…";
  return types.find((t) => t.id === d.type_id)?.name || "Type no longer in use";
}
