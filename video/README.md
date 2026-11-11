# Tekly promo video: "The Day Back"

Remotion (TypeScript). One composition, `TheDayBack`, props:

| prop | values |
|---|---|
| `cut` | `"90"` (main cut, 1:20 incl. 2 s hook), `"30"`, `"15"` (captions only, no voice) |
| `aspect` | `"16:9"`, `"1:1"`, `"9:16"` |
| `voiceoverPath` | file under `public/` or a URL; `""` for none |
| `musicPath` | file under `public/` or a URL; `""` for none |

The script lives in `src/captions.ts`: every scene, VO line and on-screen text with the script's
timecodes. Captions are generated from it, so a script change is a one-file change.

## Make it

```bash
npm install
npm run capture                         # real app clips -> public/clips (+ manifest.json)
npx tsx scripts/music.ts                # scratch score -> public/audio/music-*.wav
npx tsx scripts/vo-mix.ts <tts-dir>     # scratch VO lines -> public/audio/vo-{90,30}.wav
npm run render:all                      # 9 versions -> out/
npm run contact-sheet                   # out/contact-sheet.png (one frame per scene)
npm run verify                          # clips play, caption timing, durations
npm run studio                          # preview / tweak
```

Voice-over: Chatterbox (Resemble AI, MIT licence, runs locally; its audio carries an inaudible
Perth watermark). `scripts/chatterbox-vo.py` makes one WAV per line from `npx tsx scripts/vo-lines.ts`;
Dani (male, dry and tired) and the narrator each take a short reference clip (`--dani-ref`,
`--narrator-ref`). Setup: `uv venv --python 3.11 && uv pip install chatterbox-tts "setuptools<81"`. For the final voice-over, record one
WAV per cut, aligned to the script timecodes, and pass it as `voiceoverPath`.

Music notes (from the script): single low tone in act one; a light acoustic rhythm enters exactly on
"Same Tuesday"; a soft chime when the job flips to Approved; no swell at the logo.

## Rules this cut keeps

No competitor shown or named (act one's software is abstract grey UI). No feature or claim outside
the script: no AI features, Tekly Sense, Morning Brief, call transcription, SSO, card readers,
supplier catalogues, labour guides or statistics. Act-one shots are stock photos for the first cut.
