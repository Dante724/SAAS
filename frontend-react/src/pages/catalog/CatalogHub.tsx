// Catalog — what the shop sells: Services, Products, Job Kits. These moved out of the
// old "My company" hub into their own home. Same components, same endpoints, same
// edit drawer as before — only the location changed.
import { useState } from "react";
import Shell from "../../components/Shell";
import { useMe } from "../../context/Me";
import InventoryDrawer, { DrawerKind } from "../inventory/InventoryDrawer";
import { Services, Products, Bundles } from "../company/CompanyHub";

type Tab = "services" | "products" | "bundles";
const TABS: { id: Tab; label: string }[] = [
  { id: "services", label: "Services" },
  { id: "products", label: "Products" },
  { id: "bundles", label: "Job Kits" },
];

export default function CatalogHub() {
  const { can } = useMe();
  const tabs = TABS.filter((t) => t.id !== "services" || can("can_view_labors"));
  const [tab, setTab] = useState<Tab>(tabs[0]?.id ?? "products");
  const [drawer, setDrawer] = useState<{ kind: DrawerKind; id?: string | null; initial?: any } | null>(null);
  const [refresh, setRefresh] = useState(0);   // bumped on save so the list tabs re-fetch

  return (
    <Shell title="Catalog">
      <div className="flex-1 overflow-y-auto">
        <div className="px-6 pt-4 flex items-center gap-1 border-b border-slate-200/70">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-2 text-sm font-semibold rounded-t-lg -mb-px border-b-2 ${
                tab === t.id
                  ? "text-primary border-primary"
                  : "text-ink-tertiary border-transparent hover:text-ink-primary"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="p-6">
          {tab === "services" && can("can_view_labors") && <Services refresh={refresh} onOpen={(s) => setDrawer({ kind: "service", id: s?.id, initial: s })} />}
          {tab === "products" && <Products refresh={refresh} onOpen={(p) => setDrawer({ kind: "product", id: p?.id, initial: p })} />}
          {tab === "bundles" && <Bundles refresh={refresh} onOpen={(b) => setDrawer({ kind: "bundle", id: b?.id, initial: b })} />}
        </div>
      </div>

      {drawer && (
        <InventoryDrawer
          kind={drawer.kind}
          id={drawer.id}
          initial={drawer.initial}
          onClose={() => setDrawer(null)}
          onSaved={() => { setDrawer(null); setRefresh((n) => n + 1); }}
        />
      )}
    </Shell>
  );
}
