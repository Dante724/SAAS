import { useEffect, useState } from "react";
import { clickable } from "../lib/clickable";

// /api/media/{id} (and other media URLs) require the bearer header, which a plain <img src>
// can't send — so it 401s. This fetches the image with auth and renders it from an object URL.
// Same reason CodeSvg/QrSvg fetch their SVG instead of using <img>.
export default function AuthedImage({ src, alt, className, onClick }: { src: string; alt?: string; className?: string; onClick?: () => void }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let dead = false;
    let obj = "";
    let tok = "";
    try { tok = localStorage.getItem("ros_tok") || ""; } catch { /* ignore */ }
    fetch(src, { headers: tok ? { Authorization: `Bearer ${tok}` } : {} })
      .then((r) => (r.ok ? r.blob() : null))
      .then((b) => { if (b && !dead) { obj = URL.createObjectURL(b); setUrl(obj); } })
      .catch(() => { /* silent: the image shows its placeholder */ });
    return () => { dead = true; if (obj) URL.revokeObjectURL(obj); };
  }, [src]);
  if (!url) return <span className={`${className || ""} bg-slate-100 inline-block`} role="img" aria-label={alt || "Loading image"} />;
  return <img src={url} alt={alt || ""} className={className} {...clickable(onClick)} />;
}
