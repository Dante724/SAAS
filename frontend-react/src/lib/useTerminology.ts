// Industry terminology — the org's current asset noun (Vessel / Vehicle / Unit /
// Device / Tool / …) resolved from the selected industry template.
//
// The backend already owns the source of truth (GET /api/settings/industry-template
// → describe.asset_label). This hook fetches it ONCE, caches it module-level, and
// hands every screen the noun for app chrome (menus, table headers, "Add X" buttons,
// empty states). An individual asset that carries its own `asset_label` in its
// payload keeps the noun it was created under — pass that straight through and only
// fall back to this hook when a record has none. See assetNoun() below.
import { useEffect, useState } from "react";
import { api } from "../api/client";

export type IdentifierRule = { primary: string; allowed: string[] };
export type Terminology = { asset: string; assets: string; woButton: string; key: string; groups: { key: string; label: string; identifier?: IdentifierRule }[]; identifier: IdentifierRule };

const DEFAULT: Terminology = { asset: "Asset", assets: "Assets", woButton: "+ Order", key: "general", groups: [], identifier: { primary: "serial", allowed: ["serial"] } };

let cache: Terminology | null = null;
let inflight: Promise<Terminology> | null = null;

async function load(): Promise<Terminology> {
  if (cache) return cache;
  if (!inflight) {
    inflight = api
      .get<any>("/settings/industry-template")
      .then((r) => {
        const d = r?.describe || {};
        cache = {
          asset: d.asset_label || DEFAULT.asset,
          assets: d.asset_label_plural || DEFAULT.assets,
          woButton: d.wo_button || DEFAULT.woButton,
          key: r?.current || DEFAULT.key,
          groups: Array.isArray(d.groups) ? d.groups : [],
          identifier: d.identifier && Array.isArray(d.identifier.allowed)
            ? { primary: d.identifier.primary || "serial", allowed: d.identifier.allowed }
            : DEFAULT.identifier,
        };
        return cache;
      })
      .catch(() => DEFAULT)
      .finally(() => { inflight = null; });
  }
  return inflight;
}

// Force a refetch after the owner switches template in Settings.
export function resetTerminology() { cache = null; }

export function useTerminology(): Terminology {
  const [t, setT] = useState<Terminology>(cache || DEFAULT);
  useEffect(() => {
    let alive = true;
    load().then((v) => { if (alive) setT(v); });
    return () => { alive = false; };
  }, []);
  return t;
}

// The noun to show for one asset/record. A record created under a REAL industry
// template keeps that noun (history keeps its wording). But "general" is the
// never-chosen default — those unstamped records follow the org's current noun, so
// an org that has only ever used the default and then picks an industry actually
// sees the change everywhere.
export function assetNoun(record: { asset_label?: string | null; template_key?: string | null } | null | undefined, fallback: Terminology): string {
  const tk = record?.template_key;
  const l = record?.asset_label;
  if (!l || l === "Asset" || !tk || tk === "general") return fallback.asset;
  return l;
}
