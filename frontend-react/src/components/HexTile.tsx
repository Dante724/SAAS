import { M } from "./Shell";

// The Tekly mark's hex nut as a section's icon tile: a light sapphire fill with the icon in brand
// blue. Section headings only — it is the one place the shape is used, so it reads as a signature.
const HEX = "polygon(25% 4%, 75% 4%, 100% 50%, 75% 96%, 25% 96%, 0% 50%)";
export default function HexTile({ icon, size = 32 }: { icon: string; size?: number }) {
  return (
    <span aria-hidden="true" className="grid shrink-0 place-items-center bg-primary/[0.14] text-primary" style={{ width: size, height: size * 0.88, clipPath: HEX }}>
      <M n={icon} c="text-[15px]" />
    </span>
  );
}
