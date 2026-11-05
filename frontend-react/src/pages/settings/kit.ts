// Settings-only helpers on top of the shared pieces (SettingsPage, Section, SettingRow, Switch,
// SaveFooter, Drawer, Tabs, Button). Two card shapes, one save rule:
//   • A card of fields is a <Section> (p-4) that ends with <SaveFooter className={sectionFooter}>,
//     or a grouped card (sectionCard + divide-y + <SettingGroup>s) that ends with a plain <SaveFooter>.
//   • A lone on/off switch saves the moment it flips and confirms with the page toast.
//   • A row whose control is a wide input or select uses <SettingRow stack> so it stacks on phones.

/** SaveFooter at the bottom of a <Section> card: full-bleed line above it, text lined up with the fields. */
export const sectionFooter = "-mx-4 -mb-4 mt-4 !px-4 border-t border-slate-100";

/** A text input or select inside a SettingRow: full width on phones, a fixed width beside the label above that. */
export const rowField = "w-full sm:w-72";

// The settings page's one toast ("Saved", "Order saved"…). Any section can raise it without the
// hub passing a callback down; the hub listens and shows it. Nothing listens in a unit test.
const TOAST_EVENT = "tekly:settings-toast";
export function settingsToast(text = "Saved") {
  window.dispatchEvent(new CustomEvent(TOAST_EVENT, { detail: text }));
}
export function onSettingsToast(fn: (text: string) => void) {
  const h = (e: Event) => fn(String((e as CustomEvent).detail ?? ""));
  window.addEventListener(TOAST_EVENT, h);
  return () => window.removeEventListener(TOAST_EVENT, h);
}
