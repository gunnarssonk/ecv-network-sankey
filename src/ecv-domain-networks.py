#!/usr/bin/env python3
"""
Renders the three per-domain ECV - Networks Sankeys published with the
workbook "Observing the GCOS Essential Climate Variables".

One figure per Earth-system domain: its ECVs on the left, and on the right the
satellite data-record organisations in one group, the in situ networks that
serve only this domain in a second, and the cross-domain in situ networks in a
third.

The workbook is archived separately and can be downloaded from https://doi.org/10.5281/zenodo.21702298.
Either put it in the repository's ``data/`` directory or pass ``--workbook``.

Functions:
- resolve_workbook(explicit): Locates the workbook, or explains how to supply it.
- load_flows(workbook): Reads the sankey sheet and returns its cleaned links.
- classify(domains): Groups an in situ network by the domains it serves.
- d3_script(): Inlines the vendored plotting library.
- build_payload(flows, domain): Keeps one domain and orders its nodes and links.
- subtitle_counts(payload): Counts what the figure draws, for the subtitle.
- coverage_phrase(spec, n_ecvs): Opens the subtitle with the mapped ECVs counted against the GCOS list.
- render_html(payload, spec): Fills the HTML template with the data, the library and the domain's wording.
- build_figure(key, flows, outdir): Writes one domain's figure. Returns its path.
- parse_args(argv): Defines and parses the command-line options.
- main(argv): Reads the workbook once, builds the requested figures, opens them.

Getting started:
- Put observing-the-gcos-ecvs_v1.0.xlsx in the repository's data/, or pass --workbook PATH.
- Run: python src/ecv-domain-networks.py [atmosphere|terrestrial|ocean|all] [--outdir DIR] [--no-open]
- Edit FIGURES and SUBTITLES to change what the figures say; edit the constants at
  the top of the embedded <script> to change how they are drawn.
"""

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

import pandas as pd

__version__ = "1.0.0"

HERE = Path(__file__).resolve().parent

# ---------- INPUTS ----------
WORKBOOK_NAME = "observing-the-gcos-ecvs_v1.0.xlsx"
WORKBOOK_DOI = "https://doi.org/10.5281/zenodo.21702298"
SHEET_NAME = "5.2 ECV-Network Sankey Links"
HEADER_ROW = 4
D3_VENDOR_PATH = HERE.parent / "vendor" / "d3-selection-3.0.0.min.js"

# ---------- GROUPS ----------
SATELLITE_GROUP = "Organisations"
CROSS = "Cross-domain Networks"
DOMAINS = {
    "Atmosphere":  "Atmospheric Networks",
    "Terrestrial": "Terrestrial Networks",
    "Ocean":       "Ocean Networks",
}

# ---------- FIGURES: what differs between the three ----------
# Keyed by the command-line word; each figure is written to ecv-<key>-networks.html.
FIGURES = {
    "atmosphere":  {"domain": "Atmosphere",  "ecv_total": 16},   # domain: as written in the workbook
    "terrestrial": {"domain": "Terrestrial", "ecv_total": 20},   # ecv_total: ECVs in the GCOS list
    "ocean":       {"domain": "Ocean",       "ecv_total": 19},   #   (GCOS-245, 2022) for this domain
}
SUBTITLES = (                   # format strings over the counts from subtitle_counts(), shared by the three
    "{coverage} are observed by {n_insitu} in situ networks and "
    "{n_satellite} satellite data-record organisations ({n_links} links).",
    "Cross-domain networks also observe ECVs in other domains.",
)

# ---------- SHORT LABELS ----------
# Anything shortened here SHOULD be expanded in the figure caption.
ABBREV = {
    "Fraction of Absorbed Photosynthetically Active Radiation": "FAPAR",
    "Carbon Dioxide, Methane & Other Greenhouse Gases": "CO₂, CH₄ and other GHGs",
}


def resolve_workbook(explicit):
    """Locate the workbook, or explain how to supply it."""
    path = (explicit or HERE.parent / "data" / WORKBOOK_NAME).expanduser().resolve()
    if path.is_file():
        return path
    raise SystemExit(
        f"error: no workbook at {path}.\n"
        f"Download it from {WORKBOOK_DOI} and place it in the repository's 'data' "
        f"directory, or pass --workbook PATH."
    )


def load_flows(workbook):
    """Read the sankey sheet and return its cleaned links, one row per link."""
    columns = ["ecv_domain", "ecv (source)", "network (target)", "type (target)", "value"]
    try:
        flows = pd.read_excel(workbook, sheet_name=SHEET_NAME, header=HEADER_ROW, usecols=columns)
    except ValueError as err:  # pandas: missing sheet or missing column
        raise SystemExit(
            f"error: {err}\nExpected sheet '{SHEET_NAME}' with the columns {', '.join(columns)}."
        ) from None
    flows = flows.rename(columns={
        "ecv (source)": "ecv_source",
        "network (target)": "network",
        "type (target)": "type_target",
    })
    for c in ["ecv_domain", "ecv_source", "network", "type_target"]:
        flows[c] = flows[c].astype(str).str.strip()
    flows["value"] = pd.to_numeric(flows["value"], errors="coerce").fillna(0)

    unknown = ~flows["ecv_domain"].isin(DOMAINS)
    if unknown.any():
        print(f"warning: {int(unknown.sum())} rows skipped, ecv_domain not in {list(DOMAINS)}: "
              f"{sorted(flows.loc[unknown, 'ecv_domain'].unique())}")
    return flows[~unknown & (flows["value"] > 0)]


def classify(domains):
    """Group an in situ network by the domains it serves."""
    served = set(domains)
    return DOMAINS[served.pop()] if len(served) == 1 else CROSS


def d3_script():
    """Inline the vendored plotting library, so the figure renders offline."""
    if not D3_VENDOR_PATH.is_file():
        raise SystemExit(
            f"error: no plotting library at {D3_VENDOR_PATH}. Restore the "
            f"repository's 'vendor' directory next to 'src'."
        )
    return f"<script>\n{D3_VENDOR_PATH.read_text(encoding='utf-8').strip()}\n</script>"


def build_payload(flows, domain):
    """Keep one domain's rows, group and order its networks, list nodes and links."""
    # Grouped on all rows, so a network's cross-domain reach survives the domain filter.
    insitu_all = flows[flows["type_target"] == "In situ"]
    group_of_network = insitu_all.groupby("network")["ecv_domain"].apply(classify).to_dict()

    flows = flows[flows["ecv_domain"] == domain].copy()
    if flows.empty:
        raise SystemExit(f"error: no rows found for domain '{domain}'")

    is_insitu = flows["type_target"] == "In situ"
    flows["group"] = flows["network"].map(group_of_network).where(is_insitu, SATELLITE_GROUP)
    group_order = [SATELLITE_GROUP, DOMAINS[domain], CROSS]
    group_rank = {k: i for i, k in enumerate(group_order)}

    ecv_pairs = (
        flows[["ecv_domain", "ecv_source"]].drop_duplicates()
        .sort_values("ecv_source")
    )
    ecv_nodes = [
        {
            "name": r.ecv_source,
            "label": ABBREV.get(r.ecv_source, r.ecv_source),
            "domain": r.ecv_domain,
        }
        for r in ecv_pairs.itertuples()
    ]

    net_pairs = (
        flows[["network", "type_target", "group"]].drop_duplicates()
        .assign(_o=lambda d: d["group"].map(group_rank))
        .sort_values(["_o", "network"])
    )
    network_nodes = [
        {"name": r.network, "type": r.type_target, "group": r.group}
        for r in net_pairs.itertuples()
    ]

    agg = flows.groupby(
        ["ecv_source", "network", "type_target", "group"], as_index=False
    )["value"].sum()

    return {
        "links": [
            {
                "source": r.ecv_source,
                "target": r.network,
                "type": r.type_target,
                "group": r.group,
                "value": float(r.value),
            }
            for r in agg.itertuples()
        ],
        "ecv_nodes": ecv_nodes,
        "network_nodes": network_nodes,
    }


# ---------- HTML ----------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__DOMAIN__ ECV to Networks &amp; Organisations</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', Helvetica, Arial, sans-serif;
         background: #f2f2f0; color: #333; padding: 28px;
         display: flex; justify-content: center; }
  #chart { background: #fff; display: inline-block;
           box-shadow: 0 1px 4px rgba(0,0,0,0.10); }
  @media print {
    @page { size: A4 portrait; margin: 0; }
    body { background: #fff; padding: 0; }
    #chart { box-shadow: none; }
    .tooltip { display: none; }
  }
  svg text { font-family: 'Segoe UI', Helvetica, Arial, sans-serif; }
  .link { fill: none; transition: stroke-opacity 0.15s; }
  .link:hover { stroke-opacity: 0.95; }
  .tooltip { position: fixed; background: rgba(30,30,30,0.92); color: #eee;
    border-radius: 4px; padding: 6px 10px; font-size: 12px; pointer-events: none;
    opacity: 0; transition: opacity 0.12s; z-index: 100; line-height: 1.45; }
</style>
</head>
<body>
<div id="chart"></div>
<div id="tooltip" class="tooltip"></div>
__D3_SCRIPT__
<script>

const DATA = __DATA_JSON__;
const tooltip = document.getElementById("tooltip");

// ---- COLOUR --------------------------------------------------------------
const TYPE_COLOR      = { "Satellite": "#78afdc", "In situ": "#e6a550" };  // flows and bars
const TYPE_COLOR_DARK = { "Satellite": "#577f9f", "In situ": "#a6773a" };  // the same hues as text
const NET_BAND        = { "Satellite": "#dbe9f5", "In situ": "#fbe9d4" };  // boxes behind the network groups
const LINK_OPACITY = 0.45;

const SECTION_TITLE = "#4d4d4d";     // column titles and domain headers
const DOMAIN_SPINE  = "#b4b4b4";     // thin bar beside each domain block
const BAND          = "#f5f6f7";     // grey box behind each domain's ECVs
const INK = "#2b2b2b", MUTED = "#6b6b6b", RULE = "#d9dcdf";


// ---- TYPE -----------------------------------------------------------------
const TITLE_SIZE = 12, SUB_SIZE = 10, NOTE_SIZE = 10;
const ECV_SIZE = 9.5, NET_SIZE = 9;                 // node labels
const DOMAIN_SIZE = 9, GROUP_SIZE = 9;              // block headers and column titles
const HEADER_TRACK = 1.1, GROUP_TRACK = 0.9;        // letter-spacing


// ---- TEXT -----------------------------------------------------------------
const TITLE = "Observing __DOMAIN__ Essential Climate Variables (ECVs)";

// Written on the Python side with counts from the data; wrapped into one paragraph below.
const SUBTITLES = [
__SUBTITLE_LINES__
];

const SOURCE = "ECVs: GCOS-245 (2022). Networks: CEOS-CGMS ECV Inventory v6.0, GCOS Status Report (2027). Mapping: ESA, doi.org/10.5281/zenodo.21702298";


// ---- FLOWS ----------------------------------------------------------------
const STUB     = 10;     // flat run at the ECV end before the curve starts
const BAR_W    = 5;
const MIN_LINK = 1.5;    // thinnest link and thinnest bar, so the two always match


// ---- COLUMNS ---------------------------------------------------------------
// The right column has the most rows and sets the height. The left column
// stretches to match, between ROW_L_MIN and ROW_L_MAX.
const ROW_R      = 13;   // network row pitch; below about 10 the names get tight
const GROUP_GAP  = 30;   // btw network groups
const BAND_GAP   = 54;   // btw the SATELLITE and IN SITU bands
const DOMAIN_GAP = 30;   // btw domain blocks
const ROW_L_MIN  = 13;
const ROW_L_MAX  = 26;

const BAND_PAD     = 3;  // box overhang above the first and below the last row
const HEADER_PAD   = 10; // btw block header and its block
const TOPTITLE_GAP = 18; // btw column title and block header


// ---- PAGE ------------------------------------------------------------------
// WIDTH is fixed at A4; HEIGHT falls out of the row count at the end.
const PAGE_W = 794;      // A4 portrait at 96 dpi
const PAD_L  = 210;      // ECV label gutter
const PAD_R  = 200;      // network label gutter
const M = { top: 0, right: 20, left: 40 };  // top set below
const WIDTH  = PAGE_W;
const FLOW_W = WIDTH - M.left - PAD_L - PAD_R - M.right;

// Each step is measured off the one before it, so changing a gap moves everything below it.
const TITLE_Y       = 40;    // btw page top and title
const SUB_GAP       = 19;    // btw title and subtitle
const LINE          = 14;    // btw subtitle rows
const HEAD_RULE_GAP = 10;    // btw subtitle and the rule under the header
const HEAD_PAD      = 44;    // btw that rule and the first row
const RULE_GAP      = 28;    // btw columns and the rule above the source line
const NOTE_GAP      = 20;    // source line
const FOOT_PAD      = 40;    // bottom

// greedy wrap against real glyph widths, measured in a throwaway svg
function wrapSubtitle(text, maxW) {
  const probe = d3.select("body").append("svg")
    .style("position", "absolute").style("visibility", "hidden");
  const t = probe.append("text").attr("font-size", SUB_SIZE);
  const lines = [];
  let cur = "";
  text.split(" ").forEach(w => {
    const test = cur ? cur + " " + w : w;
    t.text(test);
    if (cur && t.node().getComputedTextLength() > maxW) { lines.push(cur); cur = w; }
    else cur = test;
  });
  if (cur) lines.push(cur);
  probe.remove();
  return lines;
}

const SUB_LINES   = wrapSubtitle(SUBTITLES.join(" "), WIDTH - M.left - M.right);
const HEAD_RULE_Y = TITLE_Y + SUB_GAP + (SUB_LINES.length - 1) * LINE + HEAD_RULE_GAP;
M.top = HEAD_RULE_Y + HEAD_PAD;

// ---- NODES AND LINKS -------------------------------------------------------
const ecvs = DATA.ecv_nodes.map((d, i) => ({ ...d, i }));
const nets = DATA.network_nodes.map((d, i) => ({ ...d, i }));
const ecvByName = Object.fromEntries(ecvs.map(d => [d.name, d]));
const netByKey  = Object.fromEntries(nets.map(d => [d.type + ":" + d.name, d]));  // a name can appear under both types

const links = DATA.links
  .filter(l => ecvByName[l.source] && netByKey[l.type + ":" + l.target])
  .map(l => ({
    ...l,
    si: ecvByName[l.source].i,
    ti: netByKey[l.type + ":" + l.target].i,
  }));

const ecvTotal = {}, netTotal = {};
links.forEach(l => {
  ecvTotal[l.si] = (ecvTotal[l.si] || 0) + l.value;
  netTotal[l.ti]     = (netTotal[l.ti]     || 0) + l.value;
});


// ---- BLOCKS ----------------------------------------------------------------
// Each domain, network group and observing type is one continuous run of rows.
const runs = (items, key) => items.reduce((acc, d) => {
  const last = acc[acc.length - 1];
  if (last && last.name === key(d)) last.items.push(d);
  else acc.push({ name: key(d), type: d.type, items: [d] });
  return acc;
}, []);

const blocks    = runs(ecvs, d => d.domain);
const groups    = runs(nets, d => d.group);
const typeBands = runs(groups, gr => gr.type);


// ---- PLACE ROWS ------------------------------------------------------------
let ry = 0;
groups.forEach((gr, gi) => {
  if (gi > 0) ry += (groups[gi - 1].type !== gr.type) ? BAND_GAP : GROUP_GAP;
  gr.top = ry;
  gr.items.forEach(d => { d.cy = ry + ROW_R / 2; ry += ROW_R; });
  gr.bot = ry;
});
const rightH = ry;

const ROW_L = Math.min(ROW_L_MAX, Math.max(ROW_L_MIN,
  (rightH - (blocks.length - 1) * DOMAIN_GAP) / ecvs.length));
let ly = 0;
blocks.forEach((b, bi) => {
  if (bi > 0) ly += DOMAIN_GAP;
  b.top = ly;
  b.items.forEach(d => { d.cy = ly + ROW_L / 2; ly += ROW_L; });
  b.bot = ly;
});
const colH = Math.max(ly, rightH);


// ---- SCALE -----------------------------------------------------------------
// One unit for both columns, so bar heights and link widths stay comparable.
const maxEcv = Math.max(...Object.values(ecvTotal));
const maxNet = Math.max(...Object.values(netTotal));
const U = Math.min((ROW_L - 3) / maxEcv, (ROW_R - 2) / maxNet);

// A link is stacked at its true pitch but drawn no thinner than MIN_LINK, so a
// bar is sized from what its strokes actually cover.
const linkPitch = l => l.value * U;
const linkW     = l => Math.max(linkPitch(l), MIN_LINK);
const overhang  = l => (linkW(l) - linkPitch(l)) / 2;
const bundleH   = ls => ls.length
  ? ls.reduce((a, l) => a + linkPitch(l), 0) + overhang(ls[0]) + overhang(ls[ls.length - 1])
  : 0;
const srcLinks = d => links.filter(l => l.si === d.i).sort((a, b) => a.ti - b.ti);
const dstLinks = d => links.filter(l => l.ti === d.i).sort((a, b) => a.si - b.si);

ecvs.forEach(d => { d.h = bundleH(srcLinks(d)); d.y0 = d.cy - d.h / 2; });
nets.forEach(d => { d.h = Math.max(MIN_LINK, bundleH(dstLinks(d))); d.y0 = d.cy - d.h / 2; });


// ---- LINK ENDS -------------------------------------------------------------
const stack = (ls, y0, key) => {
  let c = y0 + (ls.length ? overhang(ls[0]) : 0);
  ls.forEach(l => { l.w = linkW(l); l[key] = c + linkPitch(l) / 2; c += linkPitch(l); });
};
ecvs.forEach(d => stack(srcLinks(d), d.y0, "sy"));
nets.forEach(d => stack(dstLinks(d), d.y0, "ty"));

const linkPath = l => {
  const xm = (STUB + FLOW_W) / 2;
  return `M0,${l.sy}L${STUB},${l.sy}C${xm},${l.sy} ${xm},${l.ty} ${FLOW_W},${l.ty}`;
};


// ---- CANVAS ----------------------------------------------------------------
const svg = d3.select("#chart").append("svg")
  .attr("width", WIDTH).attr("xmlns", "http://www.w3.org/2000/svg");
const bg = svg.append("rect").attr("width", WIDTH).attr("fill", "#fff");

// Inside g, x = 0 is where the flows start and y = 0 is the first row; negative
// x is the left gutter. Page furniture is drawn on svg instead, off X0.
const g = svg.append("g")
  .attr("transform", `translate(${M.left + PAD_L}, ${M.top})`);

const BAND_L_X0 = -PAD_L, BAND_L_X1 = -8;
const BAND_R_X0 = FLOW_W + BAR_W + 8, BAND_R_X1 = FLOW_W + PAD_R - 8;
const BAND_L_MID = (BAND_L_X0 + BAND_L_X1) / 2;
const BAND_R_MID = (BAND_R_X0 + BAND_R_X1) / 2;
const LABEL_L_X = -12, LABEL_R_X = BAND_R_X0 + 4;


// ---- DRAW: BACKDROP --------------------------------------------------------
// grey domain band behind the ECVs
g.selectAll("rect.band").data(blocks).join("rect").attr("class", "band")
  .attr("x", BAND_L_X0).attr("y", d => d.top - BAND_PAD)
  .attr("width", BAND_L_X1 - BAND_L_X0).attr("height", d => d.bot - d.top + BAND_PAD * 2)
  .attr("fill", BAND);

g.selectAll("rect.spine").data(blocks).join("rect").attr("class", "spine")
  .attr("x", -5).attr("y", d => d.top - BAND_PAD)
  .attr("width", 2.5).attr("height", d => d.bot - d.top + BAND_PAD * 2)
  .attr("fill", DOMAIN_SPINE);

// tinted bands behind the network groups
g.selectAll("rect.netband").data(groups).join("rect").attr("class", "netband")
  .attr("x", BAND_R_X0).attr("y", d => d.top - BAND_PAD)
  .attr("width", BAND_R_X1 - BAND_R_X0).attr("height", d => d.bot - d.top + BAND_PAD * 2)
  .attr("fill", d => NET_BAND[d.type]);


// ---- DRAW: FLOWS -----------------------------------------------------------
g.append("g").selectAll("path").data(links).join("path")
  .attr("class", "link").attr("d", linkPath)
  .attr("stroke", d => TYPE_COLOR[d.type] || "#999")
  .attr("stroke-opacity", LINK_OPACITY)
  .attr("stroke-width", d => d.w)
  .on("mouseenter", (e, d) => {
    tooltip.style.opacity = 1;
    tooltip.innerHTML = `<strong>${d.source}</strong> &rarr; <strong>${d.target}</strong>` +
      `<br>${d.type}`;
  })
  .on("mousemove", e => {
    tooltip.style.left = (e.clientX + 14) + "px";
    tooltip.style.top  = (e.clientY - 10) + "px";
  })
  .on("mouseleave", () => { tooltip.style.opacity = 0; });


// ---- DRAW: MARKS -----------------------------------------------------------
// The network bars, drawn over the flows they gather.
g.append("g").selectAll("rect.net").data(nets).join("rect").attr("class", "net")
  .attr("x", FLOW_W).attr("y", d => d.y0)
  .attr("width", BAR_W).attr("height", d => d.h)
  .attr("fill", d => TYPE_COLOR[d.type] || "#666");


// ---- DRAW: TEXT ------------------------------------------------------------
// left column: its title, the domain header, then the ECV labels
g.append("text").attr("class", "coltitle")
  .attr("x", BAND_L_MID).attr("y", blocks[0].top - HEADER_PAD - TOPTITLE_GAP)
  .attr("text-anchor", "middle")
  .attr("font-size", GROUP_SIZE).attr("font-weight", 700).attr("letter-spacing", HEADER_TRACK)
  .attr("fill", SECTION_TITLE)
  .text("ECV DOMAINS");

g.selectAll("text.dom").data(blocks).join("text").attr("class", "dom")
  .attr("x", LABEL_L_X).attr("y", d => d.top - HEADER_PAD).attr("text-anchor", "end")
  .attr("font-size", DOMAIN_SIZE).attr("font-weight", 700).attr("letter-spacing", HEADER_TRACK)
  .attr("fill", SECTION_TITLE)  // same grey as the column title, so the two read as one hierarchy
  .text(d => d.name + "  (" + d.items.length + ")");

g.append("g").selectAll("text.ecv").data(ecvs).join("text").attr("class", "ecv")
  .attr("x", LABEL_L_X).attr("y", d => d.cy).attr("dy", "0.34em")
  .attr("text-anchor", "end").attr("font-size", ECV_SIZE).attr("fill", INK)
  .text(d => d.label);

// right column: the same, mirrored
g.selectAll("text.typetitle").data(typeBands).join("text").attr("class", "typetitle")
  .attr("x", BAND_R_MID).attr("y", d => d.items[0].top - HEADER_PAD - TOPTITLE_GAP)
  .attr("text-anchor", "middle")
  .attr("font-size", GROUP_SIZE).attr("font-weight", 700).attr("letter-spacing", HEADER_TRACK)
  .attr("fill", SECTION_TITLE)
  .text(d => d.name.toUpperCase());

g.selectAll("text.grouphead").data(groups).join("text").attr("class", "grouphead")
  .attr("x", LABEL_R_X).attr("y", d => d.top - HEADER_PAD)
  .attr("font-size", GROUP_SIZE).attr("font-weight", 700).attr("letter-spacing", GROUP_TRACK)
  .attr("fill", d => TYPE_COLOR_DARK[d.type] || INK)
  .text(d => d.name + "  (" + d.items.length + ")");

g.append("g").selectAll("text.netlabel").data(nets).join("text").attr("class", "netlabel")
  .attr("x", LABEL_R_X).attr("y", d => d.cy).attr("dy", "0.34em")
  .attr("font-size", NET_SIZE).attr("fill", INK)
  .text(d => d.name);


// ---- DRAW: HEADER ----------------------------------------------------------
const X0 = M.left;

svg.append("text").attr("x", X0).attr("y", TITLE_Y)
  .attr("font-size", TITLE_SIZE).attr("font-weight", 700).attr("fill", INK)
  .text(TITLE);

SUB_LINES.forEach((t, i) => {
  svg.append("text").attr("x", X0).attr("y", TITLE_Y + SUB_GAP + i * LINE)
    .attr("font-size", SUB_SIZE).attr("fill", MUTED)
    .text(t);
});

svg.append("line").attr("x1", X0).attr("x2", WIDTH - M.right)
  .attr("y1", HEAD_RULE_Y).attr("y2", HEAD_RULE_Y).attr("stroke", RULE);


// ---- DRAW: FOOTER ----------------------------------------------------------
const ruleY = M.top + colH + RULE_GAP;
svg.append("line").attr("x1", X0).attr("x2", WIDTH - M.right)
  .attr("y1", ruleY).attr("y2", ruleY).attr("stroke", RULE);

const sourceY = ruleY + NOTE_GAP;
svg.append("text").attr("x", X0).attr("y", sourceY)
  .attr("font-size", NOTE_SIZE).attr("fill", MUTED).attr("font-style", "italic")
  .text(SOURCE);

const HEIGHT = sourceY + FOOT_PAD;
svg.attr("height", HEIGHT).attr("viewBox", `0 0 ${WIDTH} ${HEIGHT}`);
bg.attr("height", HEIGHT);

</script>
</body>
</html>"""


def subtitle_counts(payload):
    """Count what the figure draws, for the subtitle."""
    nets = payload["network_nodes"]
    return {
        "n_ecvs": len(payload["ecv_nodes"]),
        "n_insitu": len({n["name"] for n in nets if n["type"] == "In situ"}),
        "n_satellite": len({n["name"] for n in nets if n["type"] == "Satellite"}),
        "n_links": int(sum(link["value"] for link in payload["links"])),
    }


def coverage_phrase(spec, n_ecvs):
    """Open the subtitle with the domain's mapped ECVs counted against the GCOS list."""
    total = spec["ecv_total"]
    name = spec["domain"].lower()
    if n_ecvs >= total:
        return f"All {n_ecvs} {name} ECVs"
    return f"Of the {total} {name} ECVs, {n_ecvs}"


def render_html(payload, spec):
    """Fill the template with the figure's data, its plotting library and the domain's wording."""
    # "</" is escaped so no label in the data can close the <script> early.
    data_json = json.dumps(payload).replace("</", "<\\/")
    counts = subtitle_counts(payload)
    counts["coverage"] = coverage_phrase(spec, counts["n_ecvs"])
    subtitle_lines = "\n".join(f"  {json.dumps(s.format(**counts))}," for s in SUBTITLES)
    return (
        HTML_TEMPLATE.replace("__D3_SCRIPT__", d3_script())
        .replace("__DATA_JSON__", data_json)
        .replace("__SUBTITLE_LINES__", subtitle_lines)
        .replace("__DOMAIN__", spec["domain"])
    )


def build_figure(key, flows, outdir):
    """Draw one domain's figure and write it to outdir. Returns the path written."""
    spec = FIGURES[key]
    payload = build_payload(flows, spec["domain"])

    outdir.mkdir(parents=True, exist_ok=True)
    output_path = outdir / f"ecv-{key}-networks.html"
    output_path.write_text(render_html(payload, spec), encoding="utf-8")

    print(f"{key}: {len(payload['ecv_nodes'])} ECV nodes, "
          f"{len(payload['network_nodes'])} network nodes, {len(payload['links'])} links")
    for grp in dict.fromkeys(x["group"] for x in payload["network_nodes"]):
        n = sum(1 for x in payload["network_nodes"] if x["group"] == grp)
        print(f"  {grp:<32} {n}")
    print(f"{key}: wrote {output_path}")
    return output_path


# ---------- COMMAND LINE ----------
def parse_args(argv=None):
    """Define and parse the command-line options."""
    parser = argparse.ArgumentParser(
        description=__doc__.strip().split("\n\n")[0],
        epilog=f"Workbook: {WORKBOOK_DOI}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "domain",
        nargs="?",
        default="all",
        choices=[*FIGURES, "all"],
        help="which domain to draw (default: all three)",
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        help=f"path to {WORKBOOK_NAME} (default: the repository's data directory)",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path.cwd(),
        help="directory to write the HTML into (default: the current directory)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="write the files without opening them in a browser",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv=None):
    """Read the workbook once, build the requested figures, open them."""
    args = parse_args(argv)
    workbook = resolve_workbook(args.workbook)
    print(f"reading {workbook}")
    flows = load_flows(workbook)

    keys = list(FIGURES) if args.domain == "all" else [args.domain]
    outdir = args.outdir.expanduser().resolve()
    written = [build_figure(key, flows, outdir) for key in keys]

    if not args.no_open:
        for path in written:
            try:
                # The query string defeats browser caching of the previous run's file.
                webbrowser.open_new_tab(f"{path.as_uri()}?v={int(time.time())}")
            except webbrowser.Error:
                pass  # headless -- the files are written either way
    return 0


if __name__ == "__main__":
    sys.exit(main())
