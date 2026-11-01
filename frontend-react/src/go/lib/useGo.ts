// Shared hooks for Tekly Go: status names/colours, offline-queue state, screen width.
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "../../api/client";
import { goQueue, type QueueStatus } from "./offlineQueue";
import type { ToastMsg } from "../components/ui";

export type StatusMeta = {
  id: string; key: string; name: string; color?: string; status_group?: string;
  sort_order?: number; is_terminal?: boolean; is_default?: boolean;
};

// Status lists change rarely: fetch once per record type per app session.
const statusCache = new Map<string, Promise<StatusMeta[]>>();
export function loadStatuses(recordType: "work_order" | "estimate"): Promise<StatusMeta[]> {
  let p = statusCache.get(recordType);
  if (!p) {
    p = api.get<{ items: StatusMeta[] }>(`/statuses?record_type=${recordType}`)
      .then((r) => r.items || [])
      .catch((e) => { statusCache.delete(recordType); throw e; });
    statusCache.set(recordType, p);
  }
  return p;
}

export function useStatuses(recordType: "work_order" | "estimate" = "work_order") {
  const [list, setList] = useState<StatusMeta[]>([]);
  useEffect(() => {
    let live = true;
    loadStatuses(recordType).then((l) => { if (live) setList(l); }).catch(() => { /* silent: status labels fall back to defaults */ });
    return () => { live = false; };
  }, [recordType]);
  const byKey = new Map(list.map((s) => [s.key, s]));
  return { list, byKey };
}

export function useQueueStatus() {
  const [st, setSt] = useState<QueueStatus>({ pending: 0, online: navigator.onLine, syncing: false, conflicts: 0, held: false });
  useEffect(() => {
    const q = goQueue();
    q.status().then(setSt).catch(() => { /* silent: offline queue storage may be unavailable; subscribe() keeps it current */ });
    const off = q.subscribe((s) => setSt(s));
    const net = () => setSt((s) => ({ ...s, online: navigator.onLine }));
    window.addEventListener("online", net);
    window.addEventListener("offline", net);
    return () => { off(); window.removeEventListener("online", net); window.removeEventListener("offline", net); };
  }, []);
  return st;
}

export function useMedia(query: string) {
  const [m, setM] = useState(() => typeof window !== "undefined" && window.matchMedia?.(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setM(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return !!m;
}

/** iPad / tablet width: two panes. */
export const useWide = () => useMedia("(min-width: 768px)");

export const ToastCtx = createContext<(m: ToastMsg) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export type Staff = { id: string; name: string };
let staffCache: Promise<Staff[]> | null = null;
/** Active staff for "assign to" pickers (fetched once per app session). */
export function useStaff() {
  const [list, setList] = useState<Staff[]>([]);
  useEffect(() => {
    let live = true;
    staffCache ??= api.get<Staff[]>("/staff/employees").then((r) => (Array.isArray(r) ? r : [])).catch((e) => { staffCache = null; throw e; });
    staffCache.then((l) => { if (live) setList(l); }).catch(() => { /* silent: staff picker stays empty; nothing is lost */ });
    return () => { live = false; };
  }, []);
  return list;
}

/** Tell open screens that tasks or jobs changed (a create from the "+" sheet, say). */
export const GO_CHANGED = "go:changed";
export function announceChange(kind: "tasks" | "jobs") {
  window.dispatchEvent(new CustomEvent(GO_CHANGED, { detail: kind }));
}
export function useOnChange(kind: "tasks" | "jobs", fn: () => void) {
  useEffect(() => {
    const h = (e: Event) => { if ((e as CustomEvent).detail === kind) fn(); };
    window.addEventListener(GO_CHANGED, h);
    return () => window.removeEventListener(GO_CHANGED, h);
  }, [kind, fn]);
}
