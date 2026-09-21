#!/usr/bin/env python3
"""
Renders the ECV - Network Type Sankey published with the workbook "Observing
the GCOS Essential Climate Variables".

Links are collapsed to the two observing types, satellite and in situ, without
naming any network; ecv-all-networks.py draws the same links with every
network and organisation named.

The workbook is archived separately and can be downloaded from https://doi.org/10.5281/zenodo.21702298.
Either put it in the repository's ``data/`` directory or pass ``--workbook``.

Functions:
- resolve_workbook(explicit): Locates the workbook, or explains how to supply it.
- load_flows(workbook): Reads the sankey sheet and returns its cleaned links.
- d3_script(): Inlines the vendored plotting library.
- build_payload(flows): Sums the links per ECV and observing type, lists nodes and links.
- subtitle_counts(payload): Counts what the figure draws, for the subtitle.
- render_html(payload): Fills the HTML template with the data, the library and the wording.
- build_figure(flows, outdir): Writes the figure. Returns its path.
- parse_args(argv): Defines and parses the command-line options.
- main(argv): Reads the workbook, builds the figure, opens it.

Getting started:
- Put observing-the-gcos-ecvs_v1.0.xlsx in the repository's data/, or pass --workbook PATH.
- Run: python src/ecv-network-type.py [--outdir DIR] [--no-open]
- Edit SUBTITLES to change what the figure says; edit the constants at the top of
  the embedded <script> to change how it is drawn.
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
DOMAINS = ["Atmosphere", "Terrestrial", "Ocean"]  # as written in the workbook, in drawing order
TYPE_ORDER = ["Satellite", "In situ"]

# ---------- FIGURE ----------
OUTPUT_NAME = "ecv-network-type.html"
GCOS_ECV_TOTAL = 55  # ECVs in the GCOS list (GCOS-245, 2022); the subtitle counts the mapped ones against it
SUBTITLES = (        # format strings over the counts from subtitle_counts()
    "Of the {gcos_total} GCOS ECVs, {n_ecvs} are observed by {n_insitu} in situ networks and "
    "{n_satellite} satellite data-record organisations ({n_links} links).",
    "Among them, {n_both} are observed both in situ and by satellite, {n_insitu_only} in situ only "
    "and {n_satellite_only} by satellite only.",
    "Flow width reflects the number of networks and organisations observing each ECV.",
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


def d3_script():
    """Inline the vendored plotting library, so the figure renders offline."""
    if not D3_VENDOR_PATH.is_file():
        raise SystemExit(
            f"error: no plotting library at {D3_VENDOR_PATH}. Restore the "
            f"repository's 'vendor' directory next to 'src'."
        )
    return f"<script>\n{D3_VENDOR_PATH.read_text(encoding='utf-8').strip()}\n</script>"


def build_payload(flows):
    """Sum the links per ECV and observing type, list nodes and links."""
    agg = flows.groupby(
        ["ecv_domain", "ecv_source", "type_target"], as_index=False
    )["value"].sum()

    ecv_pairs = (
        agg[["ecv_domain", "ecv_source"]].drop_duplicates()
        .assign(_o=lambda d: d["ecv_domain"].map(
            {k: i for i, k in enumerate(DOMAINS)}
        ))
        .sort_values(["_o", "ecv_source"])
    )
    ecv_nodes = [
        {
            "name": r.ecv_source,
            "label": ABBREV.get(r.ecv_source, r.ecv_source),
            "domain": r.ecv_domain,
        }
        for r in ecv_pairs.itertuples()
    ]
    type_nodes = [{"name": t} for t in TYPE_ORDER if t in set(agg["type_target"])]

    return {
        "links": [
            {"source": r.ecv_source, "target": r.type_target, "value": float(r.value)}
            for r in agg.itertuples()
        ],
        "ecv_nodes": ecv_nodes,
        "type_nodes": type_nodes,
        "n_ecvs": int(flows["ecv_source"].nunique()),
        # A link's value counts networks per ECV, so the per-type totals come from the names.
        "n_networks": {k: int(v) for k, v in flows.groupby("type_target")["network"].nunique().items()},
    }


# ---------- HTML ----------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ECV to Observing Type</title>
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
const LINK_OPACITY = 0.5;            // a touch heavier than the network figures: these flows carry counts

const SECTION_TITLE = "#4d4d4d";     // column title, domain headers and the ECV counts
const DOMAIN_SPINE  = "#b4b4b4";     // thin bar beside each domain block
const BAND          = "#f5f6f7";     // grey box behind each domain's ECVs
const INK = "#2b2b2b", MUTED = "#6b6b6b", RULE = "#d9dcdf";


// ---- TYPE -----------------------------------------------------------------
const TITLE_SIZE = 12, SUB_SIZE = 10, NOTE_SIZE = 10;
const ECV_SIZE = 9.5;                               // node labels
const DOMAIN_SIZE = 9;                              // block headers, column title, type labels
const HEADER_TRACK = 1.1;                           // letter-spacing


// ---- TEXT -----------------------------------------------------------------
const TITLE = "Observing the GCOS Essential Climate Variables (ECVs)";

// Written on the Python side with counts from the data; wrapped into one paragraph below.
const SUBTITLES = [
__SUBTITLE_LINES__
];

const SOURCE = "ECVs: GCOS-245 (2022). Networks: CEOS-CGMS ECV Inventory v6.0, GCOS Status Report (2027). Mapping: ESA, doi.org/10.5281/zenodo.21702298";


// ---- FLOWS ----------------------------------------------------------------
const STUB     = 26;     // long flat run, so each ECV's flows read as a small split bar beside its label
const BAR_W    = 7;
const MIN_LINK = 1.5;    // thinnest link and thinnest bar, so the two always match


// ---- COLUMNS ---------------------------------------------------------------
const ROW        = 14;   // ECV row pitch
const DOMAIN_GAP = 30;   // btw domain blocks
const TYPE_GAP   = 50;   // smallest gap btw the two type bars
const TYPE_FILL  = 0.70; // share of the column height the two bars may take together

const BAND_PAD     = 3;  // box overhang above the first and below the last row
const HEADER_PAD   = 9;  // btw block header and its block
const TOPTITLE_GAP = 19; // btw column title and block header


// ---- PAGE ------------------------------------------------------------------
// WIDTH is fixed at A4; HEIGHT falls out of the row count at the end.
const PAGE_W = 794;      // A4 portrait at 96 dpi
const PAD_L  = 210;      // ECV label gutter
const PAD_R  = 165;      // type labels and counts
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
const ecvs  = DATA.ecv_nodes.map((d, i) => ({ ...d, i }));
const types = DATA.type_nodes.map(d => ({ ...d }));
const byName = Object.fromEntries(ecvs.map(d => [d.name, d]));

const links = DATA.links
  .filter(l => byName[l.source])
  .map(l => ({ ...l, domain: byName[l.source].domain, si: byName[l.source].i }));

const ecvTotal = {}, typeTotal = {}, typeCount = {};
links.forEach(l => {
  ecvTotal[l.si]      = (ecvTotal[l.si]      || 0) + l.value;
  typeTotal[l.target] = (typeTotal[l.target] || 0) + l.value;
  typeCount[l.target] = (typeCount[l.target] || 0) + 1;
});


// ---- BLOCKS ----------------------------------------------------------------
// Each domain is one continuous run of rows.
const runs = (items, key) => items.reduce((acc, d) => {
  const last = acc[acc.length - 1];
  if (last && last.name === key(d)) last.items.push(d);
  else acc.push({ name: key(d), items: [d] });
  return acc;
}, []);

const blocks = runs(ecvs, d => d.domain);


// ---- PLACE ROWS ------------------------------------------------------------
let ly = 0;
blocks.forEach((b, bi) => {
  if (bi > 0) ly += DOMAIN_GAP;
  b.top = ly;
  b.items.forEach(d => { d.cy = ly + ROW / 2; ly += ROW; });
  b.bot = ly;
});
const colH = ly;


// ---- SCALE -----------------------------------------------------------------
// One unit for both columns, so bar heights and link widths stay comparable.
const maxEcv  = Math.max(...Object.values(ecvTotal));
const sumType = Object.values(typeTotal).reduce((a, b) => a + b, 0);
const U = Math.min((ROW - 3) / maxEcv, (colH * TYPE_FILL) / sumType);

// A link is stacked at its true pitch but drawn no thinner than MIN_LINK, so a
// bar is sized from what its strokes actually cover.
const linkPitch = l => l.value * U;
const linkW     = l => Math.max(linkPitch(l), MIN_LINK);
const overhang  = l => (linkW(l) - linkPitch(l)) / 2;
const bundleH   = ls => ls.length
  ? ls.reduce((a, l) => a + linkPitch(l), 0) + overhang(ls[0]) + overhang(ls[ls.length - 1])
  : 0;
const TYPE_RANK = Object.fromEntries(types.map((t, i) => [t.name, i]));
const srcLinks = d => links.filter(l => l.si === d.i)
                           .sort((a, b) => TYPE_RANK[a.target] - TYPE_RANK[b.target]);
const dstLinks = t => links.filter(l => l.target === t.name).sort((a, b) => a.si - b.si);

ecvs.forEach(d => { d.h = bundleH(srcLinks(d)); d.y0 = d.cy - d.h / 2; });

// The two type bars, centred as a group in the column.
const barH = types.map(t => bundleH(dstLinks(t)));
const sumBar = barH.reduce((a, b) => a + b, 0);
const gap = Math.max(TYPE_GAP, (colH - sumBar) / (types.length + 1));
let ty = (colH - (sumBar + gap * (types.length - 1))) / 2;
types.forEach((t, i) => { t.y0 = ty; t.h = barH[i]; ty += barH[i] + gap; });


// ---- LINK ENDS -------------------------------------------------------------
const stack = (ls, y0, key) => {
  let c = y0 + (ls.length ? overhang(ls[0]) : 0);
  ls.forEach(l => { l.w = linkW(l); l[key] = c + linkPitch(l) / 2; c += linkPitch(l); });
};
ecvs.forEach(d => stack(srcLinks(d), d.y0, "sy"));
types.forEach(t => stack(dstLinks(t), t.y0, "ty"));

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
const BAND_L_MID = (BAND_L_X0 + BAND_L_X1) / 2;
const LABEL_L_X = -12, LABEL_R_X = FLOW_W + 14;


// ---- DRAW: BACKDROP --------------------------------------------------------
// grey domain bands behind the ECVs
g.selectAll("rect.band").data(blocks).join("rect").attr("class", "band")
  .attr("x", BAND_L_X0).attr("y", d => d.top - BAND_PAD)
  .attr("width", BAND_L_X1 - BAND_L_X0).attr("height", d => d.bot - d.top + BAND_PAD * 2)
  .attr("fill", BAND);

g.selectAll("rect.spine").data(blocks).join("rect").attr("class", "spine")
  .attr("x", -5).attr("y", d => d.top - BAND_PAD)
  .attr("width", 2.5).attr("height", d => d.bot - d.top + BAND_PAD * 2)
  .attr("fill", DOMAIN_SPINE);


// ---- DRAW: FLOWS -----------------------------------------------------------
g.append("g").selectAll("path").data(links).join("path")
  .attr("class", "link").attr("d", linkPath)
  .attr("stroke", d => TYPE_COLOR[d.target] || "#999")
  .attr("stroke-opacity", LINK_OPACITY)
  .attr("stroke-width", d => d.w)
  .on("mouseenter", (e, d) => {
    tooltip.style.opacity = 1;
    tooltip.innerHTML = `<strong>${d.source}</strong> &rarr; ${d.target}<br>` +
      `${Math.round(d.value)} network${d.value === 1 ? "" : "s"}`;
  })
  .on("mousemove", e => {
    tooltip.style.left = (e.clientX + 14) + "px";
    tooltip.style.top  = (e.clientY - 10) + "px";
  })
  .on("mouseleave", () => { tooltip.style.opacity = 0; });


// ---- DRAW: MARKS -----------------------------------------------------------
// The two type bars, drawn over the flows they gather.
g.append("g").selectAll("rect.type").data(types).join("rect").attr("class", "type")
  .attr("x", FLOW_W).attr("y", d => d.y0)
  .attr("width", BAR_W).attr("height", d => Math.max(MIN_LINK, d.h))
  .attr("fill", d => TYPE_COLOR[d.name] || "#4a4a4a");


// ---- DRAW: TEXT ------------------------------------------------------------
// left column: its title, the domain headers, then the ECV labels
g.append("text").attr("class", "coltitle")
  .attr("x", BAND_L_MID).attr("y", blocks[0].top - HEADER_PAD - TOPTITLE_GAP)
  .attr("text-anchor", "middle")
  .attr("font-size", DOMAIN_SIZE).attr("font-weight", 700).attr("letter-spacing", HEADER_TRACK)
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

// right column: each type's name in its own hue, and its ECV count given
// "of" the total, since the two types overlap and add up to more than it
const tl = g.append("g").selectAll("g").data(types).join("g")
  .attr("transform", d => `translate(${LABEL_R_X}, ${d.y0 + d.h / 2})`);
tl.append("text").attr("dy", "-0.15em")
  .attr("font-size", DOMAIN_SIZE).attr("font-weight", 700).attr("letter-spacing", HEADER_TRACK)
  .attr("fill", d => TYPE_COLOR_DARK[d.name] || INK)
  .text(d => d.name.toUpperCase());
tl.append("text").attr("dy", "1.25em")
  .attr("font-size", DOMAIN_SIZE).attr("fill", SECTION_TITLE)
  .text(d => (typeCount[d.name] || 0) + " of " + DATA.n_ecvs + " ECVs");


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
    insitu = {link["source"] for link in payload["links"] if link["target"] == "In situ"}
    satellite = {link["source"] for link in payload["links"] if link["target"] == "Satellite"}
    return {
        "gcos_total": GCOS_ECV_TOTAL,
        "n_ecvs": len(payload["ecv_nodes"]),
        "n_insitu": payload["n_networks"].get("In situ", 0),
        "n_satellite": payload["n_networks"].get("Satellite", 0),
        "n_links": int(sum(link["value"] for link in payload["links"])),
        "n_both": len(insitu & satellite),
        "n_insitu_only": len(insitu - satellite),
        "n_satellite_only": len(satellite - insitu),
    }


def render_html(payload):
    """Fill the template with the figure's data, its plotting library and its wording."""
    # "</" is escaped so no label in the data can close the <script> early.
    data_json = json.dumps(payload).replace("</", "<\\/")
    counts = subtitle_counts(payload)
    subtitle_lines = "\n".join(f"  {json.dumps(s.format(**counts))}," for s in SUBTITLES)
    return (
        HTML_TEMPLATE.replace("__D3_SCRIPT__", d3_script())
        .replace("__DATA_JSON__", data_json)
        .replace("__SUBTITLE_LINES__", subtitle_lines)
    )


def build_figure(flows, outdir):
    """Draw the figure and write it to outdir. Returns the path written."""
    payload = build_payload(flows)

    outdir.mkdir(parents=True, exist_ok=True)
    output_path = outdir / OUTPUT_NAME
    output_path.write_text(render_html(payload), encoding="utf-8")

    print(f"{len(payload['ecv_nodes'])} ECV nodes, "
          f"{len(payload['type_nodes'])} type nodes, {len(payload['links'])} links")
    for kind, n in payload["n_networks"].items():
        print(f"  {kind:<32} {n}")
    print(f"wrote {output_path}")
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
        help="write the file without opening it in a browser",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv=None):
    """Read the workbook, build the figure, open it."""
    args = parse_args(argv)
    workbook = resolve_workbook(args.workbook)
    print(f"reading {workbook}")
    flows = load_flows(workbook)

    path = build_figure(flows, args.outdir.expanduser().resolve())

    if not args.no_open:
        try:
            # The query string defeats browser caching of the previous run's file.
            webbrowser.open_new_tab(f"{path.as_uri()}?v={int(time.time())}")
        except webbrowser.Error:
            pass  # headless -- the file is written either way
    return 0


if __name__ == "__main__":
    sys.exit(main())
