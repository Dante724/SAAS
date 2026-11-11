// Catalog — what the shop sells: Services, Products, Job Kits. These moved out of the
// old "My company" hub into their own home. Same components, same endpoints, same
// edit drawer as before — only the location changed.
import { useState } from "react";
import Shell from "../../components/Shell";
import Tabs from "../../components/Tabs";
import { useMe } from "../../context/Me";
import InventoryDrawer, { DrawerKind } from "../inventory/InventoryDrawer";
import { Services, Products, Bundles } from "../company/CompanyHub";
import PlansTab from "../memberships/PlansTab";

type Tab = "services" | "products" | "bundles" | "plans";
const TABS: { id: Tab; label: string }[] = [
  { id: "services", label: "Services" },
  { id: "products", label: "Products" },
  { id: "bundles", label: "Job kits" },
  { id: "plans", label: "Plans" },
];

export default function CatalogHub() {
  const { can } = useMe();
  const tabs = TABS.filter((t) => t.id !== "services" || can("can_view_labors"));
  const [tab, setTab] = useState<Tab>(() => (new URLSearchParams(window.location.search).get("tab") === "plans" ? "plans" : tabs[0]?.id ?? "products"));
  const [drawer, setDrawer] = useState<{ kind: DrawerKind; id?: string | null; initial?: any } | null>(null);
  const [refresh, setRefresh] = useState(0);   // bumped on save so the list tabs re-fetch

  return (
    <Shell title="Catalog">
      <div className="flex-1 overflow-y-auto">
        <Tabs label="Catalog sections" className="px-6 pt-4" value={tab} onChange={setTab} tabs={tabs} />

        <div className="p-6">
          {tab === "services" && can("can_view_labors") && <Services refresh={refresh} onOpen={(s) => setDrawer({ kind: "service", id: s?.id, initial: s })} />}
          {tab === "products" && <Products refresh={refresh} onOpen={(p) => setDrawer({ kind: "product", id: p?.id, initial: p })} />}
          {tab === "plans" && <PlansTab />}
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
