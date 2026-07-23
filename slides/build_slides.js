"use strict";
const path = require("path");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const Fi = require("react-icons/fi");
const PptxGenJS = require("pptxgenjs");

// ---------- palette (drawn from the source infographic) ----------
const C = {
  bg:      "F1EFE6", // warm cream background
  bgDeep:  "E9E7DA", // slightly deeper cream for panels
  card:    "FBFAF4", // near-white card
  navy:    "273A7A", // dominant deep cobalt-navy
  navyDk:  "1E2C5C",
  blue:    "3B5AA6", // supporting blue
  olive:   "8B8C56", // secondary moss/olive
  oliveDk: "6F7043",
  sage:    "B4B58A", // light sage
  ink:     "24241E", // near-black text
  muted:   "6E6E62", // muted label text
  line:    "D6D4C6", // hairline
  white:   "FFFFFF",
};

// tier accent by group
const TIER = {
  det: C.navy,   // stages 1-3
  sem: C.blue,   // stages 4-6
  ass: C.olive,  // stages 7-9
};

// ---------- icon rasterization ----------
const iconCache = {};
async function icon(comp, hex, px = 256) {
  const key = comp.name + hex + px;
  if (iconCache[key]) return iconCache[key];
  let svg = ReactDOMServer.renderToStaticMarkup(
    React.createElement(comp, { size: px, color: "#" + hex })
  );
  svg = svg.replace(/currentColor/g, "#" + hex);
  const buf = await sharp(Buffer.from(svg)).resize(px, px).png().toBuffer();
  const uri = "image/png;base64," + buf.toString("base64");
  iconCache[key] = uri;
  return uri;
}

// ---------- helpers ----------
function shadow() {
  return { type: "outer", color: "9A9788", blur: 7, offset: 3, angle: 90, opacity: 0.28 };
}

// numbered stage badge (circle with icon) + label rows are drawn inline per slide

async function main() {
  const pptx = new PptxGenJS();
  pptx.defineLayout({ name: "W", width: 13.333, height: 7.5 });
  pptx.layout = "W";
  pptx.author = "Tripwire";
  pptx.title = "Tripwire — 9-Stage Filter-Funnel Architecture";

  const HEAD = "Arial";
  const BODY = "Arial";

  // Pre-render icons we need
  const ICO = {
    search:  await icon(Fi.FiSearch,  C.white),
    share:   await icon(Fi.FiShare2,  C.white),
    send:    await icon(Fi.FiSend,    C.white),
    chart:   await icon(Fi.FiBarChart2, C.white),
    chartNavy: await icon(Fi.FiBarChart2, C.navy),
    activity:await icon(Fi.FiActivity,C.white),
    hash:    await icon(Fi.FiHash,    C.white),
    file:    await icon(Fi.FiFileText,C.white),
    target:  await icon(Fi.FiTarget,  C.white),
    merge:   await icon(Fi.FiGitMerge,C.white),
    layers:  await icon(Fi.FiLayers,  C.white),
    cpu:     await icon(Fi.FiCpu,     C.white),
    mail:    await icon(Fi.FiMail,    C.white),
    arrow:   await icon(Fi.FiArrowRight, C.white),
  };

  // =====================================================================
  // Reusable: circular icon badge
  // =====================================================================
  function badge(slide, x, y, d, fill, iconUri) {
    slide.addShape("ellipse", { x, y, w: d, h: d, fill: { color: fill }, line: { type: "none" }, shadow: shadow() });
    const ip = d * 0.28;
    slide.addImage({ data: iconUri, x: x + ip, y: y + ip, w: d - 2 * ip, h: d - 2 * ip });
  }

  // Reusable: small mini-funnel indicator (three bars, one highlighted)
  function miniFunnel(slide, x, y, activeIdx) {
    const cols = [C.navy, C.blue, C.olive];
    const widths = [1.35, 1.05, 0.75];
    const cx = x + 0.9;
    const bh = 0.24, gap = 0.11;
    for (let i = 0; i < 3; i++) {
      const w = widths[i];
      const active = i === activeIdx;
      slide.addShape("roundRect", {
        x: cx - w / 2, y: y + i * (bh + gap), w, h: bh, rectRadius: 0.05,
        fill: { color: active ? cols[i] : "E4E2D2" },
        line: active ? { type: "none" } : { color: "BEBFA0", width: 1.25 },
      });
    }
  }

  // =====================================================================
  // SLIDE 1 — Overview of all 9 stages + funnel
  // =====================================================================
  {
    const s = pptx.addSlide();
    s.background = { color: C.bg };

    // Title block
    s.addText("TRIPWIRE", {
      x: 0.55, y: 0.34, w: 8, h: 0.7, fontFace: HEAD, fontSize: 41, bold: true,
      color: C.navy, charSpacing: 2, margin: 0,
    });
    s.addText("The 9-Stage Filter-Funnel Architecture", {
      x: 0.57, y: 1.06, w: 8.6, h: 0.5, fontFace: HEAD, fontSize: 21, bold: true,
      color: C.ink, margin: 0,
    });
    s.addText("An autonomous IP-monitoring pipeline that reserves expensive analysis for the few changes that survive cheaper upstream gates.", {
      x: 0.57, y: 1.56, w: 7.6, h: 0.55, fontFace: BODY, fontSize: 12.5, color: C.muted, margin: 0,
    });

    // ---- Left: three tier blocks ----
    const tiers = [
      {
        color: C.navy, ico: ICO.search, name: "DETECTION & PRE-PROCESSING", stages: "STAGES 1–3",
        rows: [
          ["1 · Metadata Probe", "Cheapest signals detect if a source changed."],
          ["2 · Change Detection", "Hash + word-diff filter out cosmetic edits."],
          ["3 · Diff Generation", "A clean, normalised diff per source type."],
        ],
      },
      {
        color: C.blue, ico: ICO.share, name: "SEMANTIC FILTERING", stages: "STAGES 4–6",
        rows: [
          ["4 · Relevance Scoring", "Keyword + semantic fusion ranks candidates."],
          ["5 · Bi-Encoder Matching", "Coarse chunk pass finds matching IPFR pages."],
          ["6 · Cross-Encoder Refinement", "Precise rerank plus graph propagation."],
        ],
      },
      {
        color: C.olive, ico: ICO.send, name: "ASSESSMENT & ALERTING", stages: "STAGES 7–9",
        rows: [
          ["7 · Trigger Aggregation", "Group every trigger by IPFR page."],
          ["8 · LLM Assessment", "A verdict, confidence, and edit suggestions."],
          ["9 · Notification", "One consolidated email with feedback links."],
        ],
      },
    ];

    let ty = 2.28;
    const blockH = 1.55, blockGap = 0.09;
    tiers.forEach((t) => {
      // tier badge
      badge(s, 0.55, ty + 0.02, 0.6, t.color, t.ico);
      // tier name + stage label
      s.addText(t.name, { x: 1.28, y: ty - 0.02, w: 5.4, h: 0.32, fontFace: HEAD, fontSize: 13.5, bold: true, color: t.color, margin: 0, valign: "middle" });
      s.addText(t.stages, { x: 6.55, y: ty - 0.02, w: 1.5, h: 0.32, fontFace: HEAD, fontSize: 10.5, bold: true, color: C.muted, align: "right", margin: 0, valign: "middle" });
      // hairline under header
      s.addShape("line", { x: 1.28, y: ty + 0.36, w: 6.77, h: 0, line: { color: C.line, width: 1 } });
      // stage rows
      let ry = ty + 0.46;
      t.rows.forEach((r) => {
        s.addText([
          { text: r[0] + "  ", options: { bold: true, color: C.ink } },
          { text: "— " + r[1], options: { color: C.muted } },
        ], { x: 1.28, y: ry, w: 6.8, h: 0.3, fontFace: BODY, fontSize: 11.5, margin: 0, valign: "middle" });
        ry += 0.335;
      });
      ty += blockH + blockGap;
    });

    // ---- Right: funnel of decreasing bars ----
    const fCx = 10.75;
    const bars = [
      { w: 4.15, col: C.navy,  lbl: "STAGES 1–3", sub: "Detection & Pre-processing" },
      { w: 3.35, col: C.blue,  lbl: "STAGES 4–6", sub: "Semantic Filtering" },
      { w: 2.55, col: C.olive, lbl: "STAGES 7–9", sub: "Assessment & Alerting" },
    ];
    let by = 2.35;
    const barH = 1.0, barGap = 0.34;
    // faint funnel silhouette behind bars
    s.addShape("triangle", {
      x: fCx - 2.35, y: 2.3, w: 4.7, h: 4.25, flipV: true,
      fill: { color: C.sage, transparency: 72 }, line: { type: "none" },
    });
    bars.forEach((b, i) => {
      s.addShape("roundRect", {
        x: fCx - b.w / 2, y: by, w: b.w, h: barH, rectRadius: 0.09,
        fill: { color: b.col }, line: { type: "none" }, shadow: shadow(),
      });
      s.addText(b.lbl, { x: fCx - b.w / 2, y: by + 0.15, w: b.w, h: 0.42, fontFace: HEAD, fontSize: 15, bold: true, color: C.white, align: "center", valign: "middle", margin: 0 });
      s.addText(b.sub, { x: fCx - b.w / 2, y: by + 0.55, w: b.w, h: 0.32, fontFace: HEAD, fontSize: 10.5, color: "E7E9F2", align: "center", valign: "middle", margin: 0 });
      // down-chevron between bars
      if (i < bars.length - 1) {
        s.addShape("triangle", { x: fCx - 0.13, y: by + barH + 0.05, w: 0.26, h: barGap - 0.1, flipV: true, fill: { color: C.sage }, line: { type: "none" } });
      }
      by += barH + barGap;
    });
    // output report card
    s.addShape("triangle", { x: fCx - 0.13, y: by + 0.0, w: 0.26, h: 0.24, flipV: true, fill: { color: C.sage }, line: { type: "none" } });
    const outY = by + 0.3;
    s.addShape("roundRect", { x: fCx - 1.0, y: outY, w: 2.0, h: 0.72, rectRadius: 0.08, fill: { color: C.card }, line: { color: C.navy, width: 1.25 }, shadow: shadow() });
    s.addImage({ data: ICO.chartNavy, x: fCx - 0.86, y: outY + 0.19, w: 0.34, h: 0.34 });
    s.addText("EMAIL REPORT", { x: fCx - 0.46, y: outY, w: 1.42, h: 0.72, fontFace: HEAD, fontSize: 11.5, bold: true, color: C.navy, align: "left", valign: "middle", margin: 0 });
  }

  // =====================================================================
  // Detail slide builder (slides 2-4)
  // =====================================================================
  async function detailSlide(opts) {
    const s = pptx.addSlide();
    s.background = { color: C.bg };
    const accent = opts.accent;

    // Header
    s.addText(opts.kicker, { x: 0.55, y: 0.42, w: 6, h: 0.3, fontFace: HEAD, fontSize: 13, bold: true, color: accent, charSpacing: 3, margin: 0 });
    s.addText(opts.title, { x: 0.53, y: 0.74, w: 9.2, h: 0.66, fontFace: HEAD, fontSize: 33, bold: true, color: C.ink, margin: 0 });
    s.addText(opts.intro, { x: 0.55, y: 1.46, w: 9.4, h: 0.5, fontFace: BODY, fontSize: 13, color: C.muted, margin: 0 });

    // mini funnel indicator top-right
    miniFunnel(s, 11.1, 0.5, opts.activeIdx);
    s.addText(opts.funnelLbl, { x: 10.7, y: 1.38, w: 2.1, h: 0.26, fontFace: HEAD, fontSize: 9.5, bold: true, color: C.muted, align: "center", margin: 0 });

    // three cards
    const cardY = 2.15, cardH = 4.95;
    const marginX = 0.55, gap = 0.36;
    const cardW = (13.333 - 2 * marginX - 2 * gap) / 3;
    opts.cards.forEach((card, i) => {
      const cx = marginX + i * (cardW + gap);
      s.addShape("roundRect", { x: cx, y: cardY, w: cardW, h: cardH, rectRadius: 0.1, fill: { color: C.card }, line: { color: C.line, width: 1 }, shadow: shadow() });

      // badge + stage label
      const bd = 0.66;
      badge(s, cx + 0.28, cardY + 0.3, bd, card.color, card.ico);
      s.addText("STAGE " + card.num, { x: cx + 0.28 + bd + 0.16, y: cardY + 0.3, w: cardW - bd - 0.7, h: 0.24, fontFace: HEAD, fontSize: 10.5, bold: true, color: card.color, charSpacing: 1, margin: 0, valign: "bottom" });
      s.addText(card.name, { x: cx + 0.28 + bd + 0.16, y: cardY + 0.54, w: cardW - bd - 0.66, h: 0.42, fontFace: HEAD, fontSize: 15.5, bold: true, color: C.ink, margin: 0, valign: "top" });

      // description
      s.addText(card.desc, { x: cx + 0.3, y: cardY + 1.18, w: cardW - 0.6, h: card.descH, fontFace: BODY, fontSize: 11.5, color: C.ink, margin: 0, lineSpacingMultiple: 1.02, valign: "top" });

      // detail bullet lines
      const bulletY = cardY + 1.18 + card.descH + 0.08;
      const bullets = card.bullets.map((b, j) => ({
        text: (b.k ? b.k + "  " : "") + b.v,
        options: {
          bullet: { code: "2022", indent: 12 },
          color: C.ink, bold: false,
          breakLine: true,
          paraSpaceAfter: 5,
        },
      }));
      // build rich runs: key bold + value muted, still bulleted
      const rich = [];
      card.bullets.forEach((b, j) => {
        if (b.k) rich.push({ text: b.k + " ", options: { bold: true, color: card.color, bullet: { code: "2022", indent: 13 }, breakLine: false, paraSpaceAfter: 6 } });
        rich.push({ text: b.v, options: { color: C.ink, bullet: b.k ? false : { code: "2022", indent: 13 }, breakLine: true, paraSpaceAfter: 6 } });
      });
      s.addText(rich, { x: cx + 0.34, y: bulletY, w: cardW - 0.62, h: cardH - (bulletY - cardY) - 0.62, fontFace: BODY, fontSize: 10.8, margin: 0, valign: "top" });

      // footer note pinned to bottom
      if (card.note) {
        s.addText(card.note, { x: cx + 0.3, y: cardY + cardH - 0.56, w: cardW - 0.6, h: 0.44, fontFace: BODY, fontSize: 9.8, italic: true, color: C.muted, margin: 0, valign: "middle" });
      }
    });

    if (opts.footer) {
      s.addText(opts.footer, { x: 0.55, y: 7.2, w: 12.2, h: 0.24, fontFace: BODY, fontSize: 9.5, italic: true, color: C.muted, align: "center", margin: 0 });
    }
  }

  // ------------------ SLIDE 2 : Stages 1-3 ------------------
  await detailSlide({
    accent: TIER.det, kicker: "STAGES 1 – 3", title: "Detection & Pre-Processing",
    intro: "Cheap, exact filters stop cosmetic noise before a single expensive operation runs.",
    activeIdx: 0, funnelLbl: "DETECT",
    cards: [
      {
        num: "1", name: "Metadata Probe", color: C.navy, ico: ICO.activity,
        desc: "Decides whether a source changed at all — using the cheapest signals available, before anything is scraped. A source is probed only when its scheduled check is due.",
        descH: 1.35,
        bullets: [
          { k: "Signals", v: "ETag · Last-Modified · Content-Length · FRL version ID · RSS pub-date" },
          { k: "Advance", v: "any change signal → Stage 2" },
        ],
        note: "No useful signal? Proceed anyway — an unnecessary scrape is cheap.",
      },
      {
        num: "2", name: "Change Detection", color: C.blue, ico: ICO.hash,
        desc: "Separates meaningful edits from cosmetic ones on webpages via a three-pass system. FRL and RSS sources skip it — their changes are already structured.",
        descH: 1.35,
        bullets: [
          { k: "1 Hash", v: "SHA-256 match ⇒ skip immediately" },
          { k: "2 Diff", v: "word-level; whitespace-only ⇒ cosmetic" },
          { k: "3 Fingerprint", v: "flags terms, $ amounts, dates, cross-refs, modal verbs" },
        ],
        note: "The fingerprint tags high vs standard — it never vetoes a real change.",
      },
      {
        num: "3", name: "Diff Generation", color: C.olive, ico: ICO.file,
        desc: "Produces a precise, source-appropriate representation of what changed, then normalises it into one canonical plain-text string for every stage that follows.",
        descH: 1.35,
        bullets: [
          { k: "Webpage", v: "unified .diff vs prior snapshot (6 kept)" },
          { k: "FRL", v: "Explanatory Statement DOCX → text" },
          { k: "RSS", v: "per-item GUID / field-level delta" },
        ],
        note: "Normalise: decode entities, collapse whitespace, NFC — never lowercased.",
      },
    ],
  });

  // ------------------ SLIDE 3 : Stages 4-6 ------------------
  await detailSlide({
    accent: TIER.sem, kicker: "STAGES 4 – 6", title: "Semantic Filtering",
    intro: "Progressively more precise — and more expensive — matching narrows the field to the pages a change truly affects.",
    activeIdx: 1, funnelLbl: "FILTER",
    cards: [
      {
        num: "4", name: "Relevance Scoring", color: C.navy, ico: ICO.target,
        desc: "Which IPFR pages could this change touch? Two signals are fused by weighted Reciprocal Rank Fusion before any heavy semantic work begins.",
        descH: 1.35,
        bullets: [
          { k: "BM25", v: "YAKE keyphrases vs full-page index" },
          { k: "Bi-encoder", v: "BGE cosine vs page embeddings" },
          { k: "RRF", v: "semantic weighted 2× keyword (k = 60)" },
        ],
        note: "Top-N + threshold + fast-pass keep a major change from being capped at 5 pages.",
      },
      {
        num: "5", name: "Bi-Encoder Matching", color: C.blue, ico: ICO.share,
        desc: "A coarse chunk-level pass. The change is chunked and BGE-encoded, then cosine-compared against every precomputed IPFR chunk embedding.",
        descH: 1.35,
        bullets: [
          { k: "Candidate if", v: "any single chunk ≥ 0.75" },
          { k: "or", v: "≥ 3 chunks from a page ≥ 0.45" },
        ],
        note: "Cheap dot products over embeddings already built during corpus ingestion.",
      },
      {
        num: "6", name: "Cross-Encoder Refinement", color: C.olive, ico: ICO.merge,
        desc: "Precise reranking of the survivors with the gte-reranker cross-encoder (8,192-token window), fusing semantic, lexical and graph signals.",
        descH: 1.35,
        bullets: [
          { k: "Rerank", v: "cross-encoder + Stage-4 lexical + quasi-graph" },
          { k: "Propagate", v: "alerts flow along edges (decay 0.45 / hop, ≤ 3 hops)" },
        ],
        note: "Neighbours only boost, never lower; pages ≥ 0.60 proceed to aggregation.",
      },
    ],
  });

  // ------------------ SLIDE 4 : Stages 7-9 ------------------
  await detailSlide({
    accent: TIER.ass, kicker: "STAGES 7 – 9", title: "Assessment & Alerting",
    intro: "Survivors are grouped, judged by a generative model, and delivered as a single actionable report.",
    activeIdx: 2, funnelLbl: "ALERT",
    cards: [
      {
        num: "7", name: "Trigger Aggregation", color: C.navy, ico: ICO.layers,
        desc: "Groups every trigger that survived Stage 6 by IPFR page — so the owner gets one alert per page and the model can reason over the combined effect of several changes.",
        descH: 1.6,
        bullets: [
          { k: "Bundle", v: "all diffs + source metadata + Stage 4–6 scores" },
          { k: "One bundle", v: "per IPFR page → Stage 8" },
        ],
        note: "Prevents duplicate notifications about the same page in one run.",
      },
      {
        num: "8", name: "LLM Assessment", color: C.blue, ico: ICO.cpu,
        desc: "One LLM call per page returns a structured verdict. UNCERTAIN is a valid, expected output — the model never guesses to resolve genuine ambiguity.",
        descH: 1.6,
        bullets: [
          { k: "Verdict", v: "CHANGE_REQUIRED · NO_CHANGE · UNCERTAIN" },
          { k: "Inputs", v: "full page + diffs + bi-/cross-encoder scores" },
          { k: "Output", v: "confidence, reasoning, suggested edits (validated JSON)" },
        ],
        note: "Fail-closed: schema-invalid twice → skip page and log to health.",
      },
      {
        num: "9", name: "Notification", color: C.olive, ico: ICO.mail,
        desc: "One consolidated email per run summarises every flagged page. If nothing is flagged, no email is sent — but the run is still fully logged.",
        descH: 1.6,
        bullets: [
          { k: "Per page", v: "sources, diff, reasoning, full edits, score evidence" },
          { k: "Sections", v: "human-review (UNCERTAIN) · rejected candidates" },
          { k: "Feedback", v: "mailto links calibrate the system over time" },
        ],
        note: "Delivered via GitHub Actions + smtplib to the content owner.",
      },
    ],
    footer: "Observation mode runs Stages 1–7 and logs scores while skipping Stages 8–9 during calibration; LLM calls that fail after retries are deferred and retried at the start of the next run.",
  });

  const out = path.join(__dirname, "Tripwire_9-Stage_Architecture.pptx");
  await pptx.writeFile({ fileName: out });
  console.log("wrote", out);
}

main().catch((e) => { console.error(e); process.exit(1); });
