// Maharashtra district SVG paths — improved shapes closer to actual geography
// ViewBox: 0 0 900 750 (wider to fit Vidarbha on the right)

export const VIEWBOX = "0 0 900 750";

export const DISTRICT_PATHS = [
  // ── KONKAN ──────────────────────────────────────────────────
  { id: 1,  name: "Mumbai City",      cx: 198, cy: 450, d: "M192,438 L206,432 L214,440 L212,452 L200,456 L192,448 Z" },
  { id: 2,  name: "Mumbai Suburban",  cx: 196, cy: 415, d: "M178,398 L208,390 L218,408 L210,428 L196,436 L182,424 Z" },
  { id: 7,  name: "Palghar",          cx: 172, cy: 360, d: "M154,332 L192,322 L208,352 L200,386 L178,396 L158,370 Z" },
  { id: 3,  name: "Thane",            cx: 228, cy: 352, d: "M192,322 L248,308 L268,340 L260,378 L230,390 L208,374 L200,350 Z" },
  { id: 4,  name: "Raigad",           cx: 222, cy: 468, d: "M198,454 L234,446 L256,464 L248,508 L222,524 L196,512 L188,478 Z" },
  { id: 5,  name: "Ratnagiri",        cx: 196, cy: 548, d: "M184,516 L224,524 L228,562 L214,602 L186,600 L172,562 Z" },
  { id: 6,  name: "Sindhudurg",       cx: 190, cy: 632, d: "M178,602 L212,608 L214,648 L196,664 L172,652 L166,622 Z" },

  // ── NORTHERN MAHARASHTRA ─────────────────────────────────────
  { id: 8,  name: "Nashik",           cx: 282, cy: 298, d: "M232,262 L302,248 L336,272 L324,330 L280,346 L242,330 L228,302 Z" },
  { id: 9,  name: "Ahmednagar",       cx: 322, cy: 380, d: "M276,346 L352,332 L386,366 L374,432 L316,450 L276,436 L268,392 Z" },
  { id: 10, name: "Dhule",            cx: 262, cy: 210, d: "M238,188 L302,178 L318,214 L298,254 L248,262 L228,232 Z" },
  { id: 11, name: "Nandurbar",        cx: 186, cy: 172, d: "M154,150 L242,144 L252,192 L240,226 L190,232 L150,202 Z" },
  { id: 12, name: "Jalgaon",          cx: 368, cy: 196, d: "M298,178 L392,162 L418,204 L396,252 L344,262 L310,244 L298,214 Z" },

  // ── WESTERN MAHARASHTRA ──────────────────────────────────────
  { id: 13, name: "Pune",             cx: 298, cy: 460, d: "M258,438 L332,448 L360,484 L348,540 L296,554 L248,528 L242,480 Z" },
  { id: 14, name: "Satara",           cx: 272, cy: 558, d: "M246,530 L300,554 L314,592 L294,632 L252,638 L222,610 L230,562 Z" },
  { id: 15, name: "Sangli",           cx: 308, cy: 638, d: "M252,638 L316,628 L352,654 L340,678 L294,682 L256,660 Z" },
  { id: 16, name: "Solapur",          cx: 370, cy: 516, d: "M332,488 L402,478 L426,520 L412,570 L356,578 L320,548 L328,502 Z" },
  { id: 17, name: "Kolhapur",         cx: 248, cy: 670, d: "M222,650 L278,644 L304,670 L288,696 L248,700 L214,680 Z" },

  // ── MARATHWADA ───────────────────────────────────────────────
  { id: 18, name: "Aurangabad",       cx: 418, cy: 354, d: "M378,324 L454,312 L476,352 L462,406 L404,420 L370,388 L372,348 Z" },
  { id: 19, name: "Jalna",            cx: 476, cy: 342, d: "M454,312 L522,302 L542,338 L528,382 L472,394 L454,360 Z" },
  { id: 25, name: "Parbhani",         cx: 530, cy: 326, d: "M522,302 L578,292 L596,326 L580,368 L526,378 L510,344 Z" },
  { id: 24, name: "Hingoli",          cx: 582, cy: 312, d: "M578,292 L632,282 L648,318 L632,354 L580,362 L562,328 Z" },
  { id: 20, name: "Beed",             cx: 432, cy: 444, d: "M390,416 L462,406 L480,448 L464,496 L406,504 L382,466 Z" },
  { id: 22, name: "Osmanabad",        cx: 442, cy: 532, d: "M402,504 L466,496 L484,534 L468,572 L412,580 L390,546 Z" },
  { id: 21, name: "Latur",            cx: 500, cy: 528, d: "M466,498 L530,490 L546,526 L530,568 L474,576 L458,540 Z" },
  { id: 23, name: "Nanded",           cx: 558, cy: 408, d: "M526,382 L594,370 L620,408 L606,452 L548,464 L518,428 L518,396 Z" },

  // ── VIDARBHA ─────────────────────────────────────────────────
  { id: 26, name: "Nagpur",           cx: 664, cy: 316, d: "M630,288 L700,276 L720,316 L704,366 L646,378 L622,342 L626,304 Z" },
  { id: 36, name: "Bhandara",         cx: 720, cy: 302, d: "M700,276 L756,268 L772,304 L756,342 L704,350 L690,316 Z" },
  { id: 35, name: "Gondia",           cx: 770, cy: 286, d: "M756,268 L810,258 L826,294 L812,328 L760,336 L744,302 Z" },
  { id: 33, name: "Chandrapur",       cx: 668, cy: 416, d: "M648,382 L706,372 L726,414 L710,460 L652,470 L626,434 Z" },
  { id: 34, name: "Gadchiroli",       cx: 726, cy: 406, d: "M706,372 L764,362 L782,406 L766,452 L710,462 L690,418 Z" },
  { id: 27, name: "Wardha",           cx: 574, cy: 354, d: "M550,326 L624,316 L640,356 L622,400 L562,410 L542,372 Z" },
  { id: 29, name: "Amravati",         cx: 524, cy: 278, d: "M492,250 L562,240 L580,278 L562,320 L504,330 L482,292 Z" },
  { id: 30, name: "Akola",            cx: 456, cy: 262, d: "M418,238 L492,228 L510,264 L492,306 L432,316 L412,278 Z" },
  { id: 31, name: "Washim",           cx: 510, cy: 346, d: "M492,308 L558,298 L574,332 L558,374 L500,384 L478,348 Z" },
  { id: 32, name: "Buldhana",         cx: 386, cy: 264, d: "M352,238 L424,226 L442,262 L422,306 L366,316 L344,278 Z" },
  { id: 28, name: "Yavatmal",         cx: 592, cy: 404, d: "M558,374 L630,364 L646,404 L630,448 L568,458 L540,418 Z" },
];

// District centroid label positions (cx, cy already set above)
export function getDistrictColor(rank) {
  if (!rank) return "#1B2537";
  if (rank === 1) return "#38BDF8";
  if (rank === 2) return "#2DD4BF";
  if (rank === 3) return "#22D3EE";
  if (rank <= 5) return "#1D9FBF";
  if (rank <= 10) return "#1A6B8A";
  return "#1B2537";
}
