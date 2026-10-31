// After a job is booked in at the counter: print the ticket, give the customer their tracking link
// (text/WhatsApp via the message sheet, the phone's share sheet, or copy), then open the job or
// book in the next one.
import { useState } from "react";
import { CheckCircle2, Copy, ExternalLink, Loader2, MessageCircle, Plus, Printer, Share2 } from "lucide-react";
import { useMe } from "../../context/Me";
import { Card, GhostButton, PrimaryButton } from "./ui";
import MessageSheet from "./MessageSheet";
import { printTicket, trackingUrl } from "../lib/ticket";
import { useToast } from "../lib/useGo";

export default function JobCreated({ job, isEstimate, customer, onOpen, onAnother }: {
  job: { id: string; order_number: string }; isEstimate: boolean; customer?: { name: string; phone?: string | null } | null;
  onOpen: () => void; onAnother: () => void;
}) {
  const { can } = useMe();
  const toast = useToast();
  const [busy, setBusy] = useState<null | "print" | "share" | "copy">(null);
  const [msg, setMsg] = useState(false);
  const maySend = can("can_send_sms");
  const canShare = typeof navigator !== "undefined" && !!navigator.share;

  async function run(kind: "print" | "share" | "copy") {
    if (!navigator.onLine) { toast({ title: "You're offline", tone: "warn" }); return; }
    setBusy(kind);
    try {
      if (kind === "print") await printTicket(job.id);
      else {
        const url = await trackingUrl(job.id);
        if (kind === "share") await navigator.share({ title: `${isEstimate ? "Estimate" : "Job"} ${job.order_number}`, text: `Track your repair ${job.order_number}`, url });
        else {
          // Some browsers refuse the clipboard; then show the link so it can be read out or copied by hand.
          try { await navigator.clipboard.writeText(url); toast({ title: "Tracking link copied" }); }
          catch { toast({ title: "Here's the tracking link", body: url }); }
        }
      }
    } catch (e: any) {
      if (e?.name !== "AbortError") toast({ title: kind === "print" ? "Couldn't print" : "Couldn't get the link", body: e?.message, tone: "error" });
    } finally { setBusy(null); }
  }

  const spin = (k: string) => busy === k && <Loader2 size={18} className="animate-spin" />;
  return (
    <div className="py-6 md:py-10 max-w-xl mx-auto text-center">
      <CheckCircle2 size={56} strokeWidth={1.5} className="mx-auto text-emerald-600" />
      <h2 className="mt-3 text-go-title font-bold text-ink-primary">{isEstimate ? "Estimate" : "Job"} {job.order_number} booked in</h2>
      {customer && <p className="mt-1 text-go-body text-ink-secondary">{customer.name}{customer.phone ? ` · ${customer.phone}` : ""}</p>}
      <Card className="mt-6 p-3 text-left">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <GhostButton onClick={() => run("print")} className="min-h-[3.5rem] justify-start">{spin("print") || <Printer size={20} />}Print ticket</GhostButton>
          {maySend && customer && <GhostButton onClick={() => setMsg(true)} className="min-h-[3.5rem] justify-start"><MessageCircle size={20} />Text the tracking link</GhostButton>}
          {canShare
            ? <GhostButton onClick={() => run("share")} className="min-h-[3.5rem] justify-start">{spin("share") || <Share2 size={20} />}Share tracking link</GhostButton>
            : <GhostButton onClick={() => run("copy")} className="min-h-[3.5rem] justify-start">{spin("copy") || <Copy size={20} />}Copy tracking link</GhostButton>}
          <GhostButton onClick={onOpen} className="min-h-[3.5rem] justify-start"><ExternalLink size={20} />Open the job</GhostButton>
        </div>
      </Card>
      <PrimaryButton onClick={onAnother} className="mt-5"><Plus size={20} />Book in another</PrimaryButton>
      <MessageSheet open={msg} onClose={() => setMsg(false)} orderId={job.id} onRead={() => {}} />
    </div>
  );
}
