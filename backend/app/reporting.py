"""Downloadable research report.

A self-contained HTML file a City Lead can read offline, email, or print to PDF
before a meeting. It leads with what is known, states the scope of every
finding, and gives unknowns their own section rather than burying them — the
gaps are the questions to ask in the room.
"""

from html import escape
from typing import Any

from .ontology import DIMENSION_KEYS, DIMENSION_LABELS


STYLE = """
:root{--ink:#172222;--muted:#687575;--line:#d8e0dc;--paper:#f5f7f2;--mint:#d8f3df;--green:#1b7654;--coral:#ed765c;--amber:#946b00}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--ink);font:16px/1.6 Georgia,serif}
.sheet{max-width:860px;margin:0 auto;padding:56px 32px 80px}
.meta{color:var(--muted);font:11px/1.6 'Courier New',monospace;text-transform:uppercase;letter-spacing:.04em}
h1{font:800 42px/1.05 Helvetica,Arial,sans-serif;letter-spacing:-1.5px;margin:18px 0 10px}
h2{font:700 20px Helvetica,Arial,sans-serif;border-bottom:1px solid var(--line);padding-bottom:8px;margin:48px 0 8px}
h3{font:700 15px Helvetica,Arial,sans-serif;margin:28px 0 6px;color:var(--green)}
p{margin:0 0 12px}
.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:26px 0 8px}
.summary div{border:1px solid var(--line);padding:12px}
.summary span{display:block;color:var(--muted);font:10px 'Courier New',monospace;text-transform:uppercase;margin-bottom:6px}
.summary strong{font:700 22px Helvetica,Arial,sans-serif}
.finding{border:1px solid var(--line);border-left:3px solid var(--green);padding:14px 18px;margin:14px 0;background:#fbfcfa;page-break-inside:avoid}
.finding.flagged{border-left-color:var(--coral)}
.claim{font-weight:700;margin-bottom:8px}
blockquote{border-left:2px solid #f6d778;margin:10px 0;padding-left:14px;color:var(--muted);font-size:14px}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.tag{font:10px 'Courier New',monospace;text-transform:uppercase;padding:4px 7px;background:var(--mint);color:var(--green)}
.tag.scope{background:#fff0d0;color:var(--amber)}
.tag.src{background:#eef1ee;color:var(--muted)}
.warn{border:1px solid #eedb9b;background:#fffbef;padding:10px 14px;margin:10px 0;color:var(--amber);font-size:14px}
ul{margin:0 0 12px;padding-left:20px}
li{margin-bottom:8px}
.gap{border-left:3px solid var(--coral);padding:10px 14px;margin:10px 0;background:#fdf7f5;page-break-inside:avoid}
.gap b{display:block;font:700 14px Helvetica,Arial,sans-serif}
.gap p{color:var(--muted);font-size:14px;margin:4px 0 0}
a{color:var(--green);word-break:break-word}
table{width:100%;border-collapse:collapse;font-size:13px;margin:12px 0}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font:10px 'Courier New',monospace;text-transform:uppercase;color:var(--green)}
footer{margin-top:56px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
@media print{.sheet{padding:0}body{font-size:11pt}}
"""

KIND_LABEL = {
    "withheld": "Withheld by the fact checker",
    "blocked": "Source not crawlable",
    "unreachable": "Source unreadable",
    "missing": "No evidence found",
}


def _finding(fact: dict[str, Any]) -> str:
    flagged = " flagged" if fact.get("scope_flag") else ""
    scope_warning = f'<div class="warn">{escape(fact["scope_flag"])}</div>' if fact.get("scope_flag") else ""
    entities = "".join(
        f'<span class="tag src">{escape(entity["type"])}: {escape(entity["name"])}</span>'
        for entity in fact.get("entities", [])[:5]
    )
    return f"""
    <div class="finding{flagged}">
      <p class="claim">{escape(fact["claim"])}</p>
      {scope_warning}
      <blockquote>&ldquo;{escape(fact["evidence_quote"])}&rdquo;</blockquote>
      <p class="meta">Source: <a href="{escape(fact["source_url"])}">{escape(fact["source_title"])}</a></p>
      <div class="tags">
        <span class="tag">{escape(str(fact.get("verification_status", "")).replace("_", " ").lower())}</span>
        <span class="tag scope">scope: {escape(fact.get("geographic_scope", "unknown"))}</span>
        <span class="tag src">confidence {fact.get("confidence", 0)}</span>
        <span class="tag src">checked by {escape(str(fact.get("verified_by", "rules")).split(":")[0])}</span>
        {entities}
      </div>
    </div>"""


def render_report(run: dict[str, Any]) -> str:
    city = run["city"]
    location = f"{city}, {run['country']}" if run.get("country") else city
    facts = run.get("facts", [])
    gaps = run.get("gaps", [])
    sources = run.get("sources", [])
    metrics = run.get("metrics", {})
    coverage = run.get("coverage", {})

    sections = []
    for key in DIMENSION_KEYS:
        dimension_facts = [fact for fact in facts if fact.get("dimension") == key]
        if not dimension_facts:
            continue
        sections.append(f"<h3>{escape(DIMENSION_LABELS[key])}</h3>" + "".join(_finding(fact) for fact in dimension_facts))
    findings_html = "".join(sections) or (
        '<div class="warn">No claim survived the independent evidence check. '
        "This report deliberately contains no findings rather than unverified assertions.</div>"
    )

    gap_groups: dict[str, list[dict[str, Any]]] = {}
    for gap in gaps:
        gap_groups.setdefault(gap.get("kind", "missing"), []).append(gap)
    gaps_html = "".join(
        f"<h3>{escape(KIND_LABEL.get(kind, kind))}</h3>"
        + "".join(
            f'<div class="gap"><b>{escape(item["topic"])}</b><p>{escape(item["reason"])}'
            + (f' <a href="{escape(item["source_url"])}">Open source</a>' if item.get("source_url") else "")
            + "</p></div>"
            for item in items[:20]
        )
        for kind, items in gap_groups.items()
    ) or "<p>No gaps were recorded.</p>"

    coverage_rows = "".join(
        f"<tr><td>{escape(DIMENSION_LABELS[key])}</td><td>{coverage.get(key, 0)}</td>"
        f"<td>{'Covered' if coverage.get(key) else 'Open question for the meeting'}</td></tr>"
        for key in DIMENSION_KEYS
    )

    source_rows = "".join(
        f'<tr><td><a href="{escape(source["url"])}">{escape(source["title"][:110])}</a></td>'
        f'<td>{"read" if source.get("fetched") else ("permitted" if source.get("crawl_allowed") else "not crawled")}</td>'
        f'<td>{escape(source.get("crawlability_reason", "")[:150])}</td></tr>'
        for source in sources[:40]
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<title>{escape(city)} — CARDIO4Cities city brief</title>
<style>{STYLE}</style></head>
<body><main class="sheet">
<p class="meta">CARDIO4Cities &middot; evidence-first city intelligence</p>
<h1>{escape(location)}</h1>
<p class="meta">Generated {escape(run.get("completed_at", ""))} &middot; status: {escape(run.get("status", ""))} &middot; {run.get("rounds", 1)} research round(s)</p>

<div class="summary">
  <div><span>Verified findings</span><strong>{len(facts)}</strong></div>
  <div><span>Declared gaps</span><strong>{len(gaps)}</strong></div>
  <div><span>Dimensions covered</span><strong>{metrics.get("dimensions_covered", 0)}/{metrics.get("dimensions_total", len(DIMENSION_KEYS))}</strong></div>
  <div><span>Sources assessed</span><strong>{len(sources)}</strong></div>
</div>

<h2>How to read this brief</h2>
<p>Every finding below carries the exact wording from its source and an independent verification verdict.
Findings marked with a scope warning are <strong>not</strong> specific to {escape(city)} and must not be
presented as city figures. Anything this system could not establish appears under <em>What we do not know</em>
rather than being filled in with an assumption.</p>

<h2>Findings</h2>
{findings_html}

<h2>What we do not know</h2>
<p>These are the open questions to take into the meeting.</p>
{gaps_html}

<h2>Dimension coverage</h2>
<table><thead><tr><th>Dimension</th><th>Verified city-level findings</th><th>Status</th></tr></thead><tbody>{coverage_rows}</tbody></table>

<h2>Sources assessed</h2>
<table><thead><tr><th>Source</th><th>Decision</th><th>Reason</th></tr></thead><tbody>{source_rows or '<tr><td colspan="3">No sources were discovered.</td></tr>'}</tbody></table>

<h2>Method</h2>
<p>Sources were discovered on the public internet at request time. Each was checked against its
<code>robots.txt</code> before any page was fetched. Claims were extracted with their supporting wording
located verbatim in the source document, then passed to an independent checker that could withhold them.
Figures that do not appear in the quoted evidence are rejected automatically. Verified findings are stored
in a relational ledger, a vector store and a Graphiti knowledge graph for reuse.</p>

<footer>Generated by the CARDIO4Cities Intelligence Studio. Findings are evidence from public sources,
not clinical or policy advice. Verify any figure in its original source before citing it externally.</footer>
</main></body></html>"""
