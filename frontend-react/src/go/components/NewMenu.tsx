// The "+" button: bottom-right, in thumb reach above the tab bar, on Today, Tasks and Inquiries.
// Opens a short sheet: New job · New estimate · New inquiry · New task (each shown only if the role may).
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ClipboardList, FileText, ListTodo, PhoneIncoming, Plus } from "lucide-react";
import { useMe } from "../../context/Me";
import { Sheet } from "./ui";
import TaskSheet from "./TaskSheet";
import InquirySheet from "./InquirySheet";

export default function NewMenu() {
  const nav = useNavigate();
  const { can } = useMe();
  const [open, setOpen] = useState(false);
  const [task, setTask] = useState(false);
  const [inquiry, setInquiry] = useState(false);
  const rows = [
    can("can_create_tickets") && { key: "job", label: "New job", hint: "Book in a repair", Icon: ClipboardList, go: () => nav("/new/job") },
    can("can_create_tickets") && can("can_create_estimates") && { key: "est", label: "New estimate", hint: "Quote before the work", Icon: FileText, go: () => nav("/new/estimate") },
    { key: "inq", label: "New inquiry", hint: "Someone asked about a repair", Icon: PhoneIncoming, go: () => setInquiry(true) },
    { key: "task", label: "New task", hint: "For you or a teammate", Icon: ListTodo, go: () => setTask(true) },
  ].filter(Boolean) as { key: string; label: string; hint: string; Icon: typeof Plus; go: () => void }[];

  return (
    <>
      <button onClick={() => setOpen(true)} aria-label="New"
        className="fixed z-30 right-[calc(env(safe-area-inset-right)+1rem)] bottom-[calc(env(safe-area-inset-bottom)+4.75rem)] h-14 w-14 rounded-full bg-primary text-white shadow-xs inline-flex items-center justify-center active:bg-primary-container transition-colors">
        <Plus size={28} strokeWidth={2.25} />
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title="New">
        <div className="space-y-1">
          {rows.map((r) => (
            <button key={r.key} onClick={() => { setOpen(false); r.go(); }}
              className="w-full min-h-[3.75rem] flex items-center gap-3 rounded-xl px-3 text-left active:bg-slate-100">
              <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-primary"><r.Icon size={20} /></span>
              <span className="flex-1">
                <span className="block text-go-body font-semibold text-ink-primary">{r.label}</span>
                <span className="block text-go-sub text-ink-secondary">{r.hint}</span>
              </span>
            </button>
          ))}
        </div>
      </Sheet>
      <TaskSheet open={task} onClose={() => setTask(false)} />
      <InquirySheet open={inquiry} onClose={() => setInquiry(false)} />
    </>
  );
}
