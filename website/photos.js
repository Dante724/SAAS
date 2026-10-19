/* Photo manifest. Real photos ("-cc" files, Wikimedia Commons) were chosen by a three-reviewer
   council (art director, shop owner, conversion marketer) from 224 licensed candidates, with
   picks vetoed if they showed military/government imagery or prominent third-party branding.
   Slots without a suitable real photo keep the illustrative image. Each slot lists sources in
   order; the first file that loads wins. Credits render on the photo and in the footer.
   assets/web/*.webp are the served versions (1400w + 720w); real photos there are reframed and
   colour-graded to one house look, so their credits say "adapted". */
window.TEKLY_PHOTOS = {
  "phone": { src: ["assets/web/phone-1400.webp", "assets/photos/phone-cc.jpg", "assets/img/hero-bench.jpg"], credit: {"text": "Redaktion NdW", "url": "https://commons.wikimedia.org/wiki/File:Repair_Lab_(48115787881).jpg", "adapted": true, "license": "CC BY 2.0", "licenseUrl": "https://creativecommons.org/licenses/by/2.0"} },
  "computer": { src: ["assets/web/computer-1400.webp", "assets/photos/computer-cc.jpg", "assets/photos/computer.jpg"], credit: {"text": "EnricoCastelli", "url": "https://commons.wikimedia.org/wiki/File:Hacker_repairs_a_computer_-_Linux_Day_in_Torino_2022.jpg", "adapted": true, "license": "CC0", "licenseUrl": "http://creativecommons.org/publicdomain/zero/1.0/deed.en"} },
  "tools": { src: ["assets/web/tools-1400.webp", "assets/img/tools.jpg"] },
  "small-engine": { src: ["assets/web/small-engine-1400.webp", "assets/photos/small-engine-cc.jpg", "assets/photos/small-engine.jpg"], credit: {"text": "Shixart1985", "url": "https://commons.wikimedia.org/wiki/File:Man_repairs_lawn_mower_in_garden.jpg", "adapted": true, "license": "CC BY 2.0", "licenseUrl": "https://creativecommons.org/licenses/by/2.0"} },
  "powersports": { src: ["assets/web/powersports-1400.webp", "assets/img/powersports.jpg"] },
  "auto": { src: ["assets/web/auto-1400.webp", "assets/photos/auto-cc.jpg", "assets/img/auto.jpg"], credit: {"text": "Photokadaffi", "url": "https://commons.wikimedia.org/wiki/File:Femme_mecanicienne33.jpg", "adapted": true, "license": "CC BY-SA 4.0", "licenseUrl": "https://creativecommons.org/licenses/by-sa/4.0"} },
  "bike": { src: ["assets/web/bike-1400.webp", "assets/photos/bike-cc.jpg", "assets/img/bike.jpg"], credit: {"text": "Jorge Royan", "url": "https://commons.wikimedia.org/w/index.php?curid=23087855", "adapted": true, "license": "CC BY-SA 3.0", "licenseUrl": "https://creativecommons.org/licenses/by-sa/3.0/"} },
  "motorcycle": { src: ["assets/web/motorcycle-1400.webp", "assets/photos/motorcycle-cc.jpg", "assets/img/motorcycle.jpg"], credit: {"text": "Shixart1985", "url": "https://commons.wikimedia.org/wiki/File:Mechanic_repairs_motorcycle_while_enjoying_a_sunny_afternoon.jpg", "adapted": true, "license": "CC BY 2.0", "licenseUrl": "https://creativecommons.org/licenses/by/2.0"} },
  "marine": { src: ["assets/web/marine-1400.webp", "assets/img/marine.jpg"] },
  "fleet": { src: ["assets/web/fleet-1400.webp", "assets/img/fleet.jpg"] },
  "general": { src: ["assets/web/general-1400.webp", "assets/photos/general-cc.jpg", "assets/img/general.jpg"], credit: {"text": "Karen Blakeman", "url": "https://commons.wikimedia.org/wiki/File:Busy_day_at_Reading_Repair_Cafe.jpg", "adapted": true, "license": "CC0", "licenseUrl": "http://creativecommons.org/publicdomain/zero/1.0/deed.en"} },
  "pickup": { src: ["assets/web/pickup-1400.webp", "assets/img/pickup.jpg"] },
  "owner": { src: ["assets/web/owner-1400.webp", "assets/img/owner.jpg"] },
  "owner2": { src: ["assets/web/owner-1400.webp", "assets/img/owner.jpg"] },
  "parts": { src: ["assets/web/parts-1400.webp", "assets/img/parts.jpg"] },
};
