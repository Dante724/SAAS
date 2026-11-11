"""Voice-over with Chatterbox (Resemble AI, MIT licence, runs locally; outputs carry its
inaudible Perth watermark). One WAV per line, named <cut>-<segment>.wav, for scripts/vo-mix.ts.

  python scripts/chatterbox-vo.py <lines.json> <out-dir> [--only 90-cold-open,...]
      [--dani-ref dani.wav] [--narrator-ref narrator.wav]

lines.json comes from `npx tsx scripts/vo-lines.ts`. Without a reference clip a speaker uses
Chatterbox's built-in voice; with one (5–15 s of clean speech) it takes that voice.
Dani: tired but dry, so low exaggeration and a slower pace. Narrator: even and warm.
"""
import argparse
import json
import os

import torch
import torchaudio as ta
from chatterbox.tts import ChatterboxTTS

STYLE = {
    "dani": dict(exaggeration=0.35, cfg_weight=0.35, temperature=0.7),
    "narrator": dict(exaggeration=0.5, cfg_weight=0.5, temperature=0.6),
}

ap = argparse.ArgumentParser()
ap.add_argument("lines")
ap.add_argument("out")
ap.add_argument("--only", default="")
ap.add_argument("--dani-ref", default=None)
ap.add_argument("--narrator-ref", default=None)
ap.add_argument("--seed", type=int, default=7)
a = ap.parse_args()

device = "mps" if torch.backends.mps.is_available() else "cpu"
model = ChatterboxTTS.from_pretrained(device=device)
refs = {"dani": a.dani_ref, "narrator": a.narrator_ref}
only = set(x for x in a.only.split(",") if x)
os.makedirs(a.out, exist_ok=True)

for line in json.load(open(a.lines)):
    if only and line["name"] not in only:
        continue
    torch.manual_seed(a.seed)
    spk = line.get("speaker", "dani")
    wav = model.generate(line["text"], audio_prompt_path=refs.get(spk), **STYLE[spk])
    path = os.path.join(a.out, f"{line['name']}.wav")
    ta.save(path, wav, model.sr)
    print(line["name"], f"{wav.shape[-1] / model.sr:.2f}s", flush=True)
