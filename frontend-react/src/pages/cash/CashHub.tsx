// Standalone /cash route — the cash panel in its own Shell. The same CashPanel is also hosted as the
// "Cashboxes" tab inside FinanceHub (144bj), so Cash & drawer lives under one Finance menu.
import Shell from "../../components/Shell";
import CashPanel from "./CashPanel";

export default function CashHub() {
  return (
    <Shell title="Cash & drawer">
      <CashPanel />
    </Shell>
  );
}
