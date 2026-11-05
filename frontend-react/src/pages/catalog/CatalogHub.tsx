// Catalog — what the shop sells: Services, Products, Job Kits. These moved out of the
// old "My company" hub into their own home. Same components, same endpoints, same
// edit drawer as before — only the location changed.
import { useState } from "react";
import Shell from "../../components/Shell";
import PageLayout from "../../components/PageLayout";
import Tabs from "../../components/Tabs";
import { useMe } from "../../context/Me";
import InventoryDrawer, { DrawerKind } from "../inventory/InventoryDrawer";
import { Services, Products, Bundles } from "../company/CompanyHub";
import PlansTab from "../memberships/PlansTab";

type Tab = "services" | "products" | "bundles" | "plans";
const TABS: { id: Tab; label: string }[] = [
  { id: "services", label: "Services" },
  { id: "products", label: "Products" },
  { id: "bundles", label: "Job Kits" },
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
      {/* The section tabs stay put above the scroll area; every tab opens with the same toolbar. */}
      <div className="px-6 flex-shrink-0">
        <Tabs look="underline" label="Catalog sections" value={tab} onChange={setTab} tabs={tabs} />
      </div>
      <PageLayout>
        {tab === "services" && can("can_view_labors") && <Services refresh={refresh} onOpen={(s) => setDrawer({ kind: "service", id: s?.id, initial: s })} />}
        {tab === "products" && <Products refresh={refresh} onOpen={(p) => setDrawer({ kind: "product", id: p?.id, initial: p })} />}
        {tab === "plans" && <PlansTab />}
        {tab === "bundles" && <Bundles refresh={refresh} onOpen={(b) => setDrawer({ kind: "bundle", id: b?.id, initial: b })} />}
      </PageLayout>

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
