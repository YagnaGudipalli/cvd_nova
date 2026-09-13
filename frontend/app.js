/* CARDIO4Cities Intelligence Studio — workspace client.
 *
 * The user is a City Lead, not an engineer. Two rules shape everything here:
 * findings and their gaps sit together, and nothing is ever presented with more
 * confidence than the backend attached to it. Scope warnings and degraded
 * runtime modes are rendered prominently rather than tucked away.
 */

const API_BASE = window.CARDIO_API_BASE || "";
// Must match backend/app/version.py and the ?v= on this file in index.html.
const APP_VERSION = "2.2.0";
const $ = (selector) => document.querySelector(selector);

let researchResult = null;
let workflowStructure = null;
let selectedStep = 0;

const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[character]));

const STATUS_LABEL = {
  SUPPORTED: "Supported",
  PARTIALLY_SUPPORTED: "Partially supported",
  SCOPE_MISMATCH: "Not city-specific",
  GRAPH_RELATIONSHIP: "Graph relationship",
  UNSUPPORTED: "Unsupported"
};

const KIND_LABEL = {
  withheld: "Withheld by the checker",
  blocked: "Not crawlable",
  unreachable: "Could not be read",
  missing: "Nothing found"
};

const friendly = (value) => STATUS_LABEL[value] || String(value ?? "").replaceAll("_", " ").toLowerCase();

async function getJSON(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Request failed (${response.status})`);
  return response.json();
}

/* ------------------------------------------------------------------ */
/* Runtime honesty banner                                              */
/* ------------------------------------------------------------------ */

function renderRuntimeBanner(runtime) {
  const banner = $("#runtime-banner");
  if (!banner || !runtime) return;
  const mode = runtime.reasoning?.mode;
  if (mode === "llm") {
    banner.classList.add("hidden");
    return;
  }
  banner.classList.remove("hidden");
  banner.innerHTML = `
    <strong>Reduced capability mode</strong>
    <p>${esc(runtime.reasoning?.detail || "")} Findings are sentences copied verbatim from sources rather than written summaries, and the knowledge graph is not being written.</p>`;
}

function renderRuntimeDetail(runtime) {
  const target = $("#runtime-modes");
  if (!target || !runtime) return;
  const providers = (runtime.discovery || []).map((provider) => `
    <div class="store-row">
      <div><strong>${esc(provider.name)}</strong><span>${esc(provider.role)}</span></div>
      <b class="store-status ${provider.available ? "" : "not_configured"}">${provider.available ? "active" : "not configured"}</b>
    </div>`).join("");
  target.innerHTML = `
    <div class="store-row">
      <div><strong>Reasoning</strong><span>${esc(runtime.reasoning?.detail || "")}</span></div>
      <b class="store-status ${runtime.reasoning?.mode === "llm" ? "" : "not_configured"}">${esc(runtime.reasoning?.mode || "unknown")}</b>
    </div>
    <div class="store-row">
      <div><strong>Embeddings</strong><span>${esc(runtime.embeddings?.mode)} · ${esc(runtime.embeddings?.dimensions)}d · ${esc(runtime.embeddings?.collection)}</span></div>
      <b class="store-status">${runtime.embeddings?.mode === "lexical-hash" ? "fallback" : "model"}</b>
    </div>
    ${providers}`;
}

/* ------------------------------------------------------------------ */
/* Workflow                                                            */
/* ------------------------------------------------------------------ */

// Plain-language framing for each stage, keyed by the name the backend records.
// The technical name stays visible as secondary text so the two can be matched.
const STEP_GUIDE = {
  "Intake and guardrails": { title: "Check the request", phase: "Plan", why: "Confirms the place is real and fixes the rules the run must follow, before anything touches the internet." },
  "Research planning": { title: "Decide what to search for", phase: "Plan", why: "Turns the city into searches across the five areas of city health. A second round only targets the areas still missing." },
  "Live source discovery": { title: "Find sources", phase: "Gather", why: "Searches the live web and ranks what it finds by how trustworthy the publisher is." },
  "Crawlability gate": { title: "Ask permission", phase: "Gather", why: "Reads each site's robots.txt first. Sites that refuse automated access are never fetched." },
  "Evidence extraction": { title: "Read the pages", phase: "Gather", why: "Downloads the permitted pages, best sources first and within the depth you chose, and keeps clean text with a timestamp." },
  "Claim extraction": { title: "Pull out findings", phase: "Verify", why: "Proposes specific statements with an exact quote. A quote that cannot be found in the page is discarded, which stops invented citations." },
  "Independent fact check": { title: "Double-check each finding", phase: "Verify", why: "A separate checker, which wrote none of the findings, decides what is supported, what needs a scope warning and what is withheld." },
  "Sufficiency review": { title: "Is it enough?", phase: "Verify", why: "Counts the areas with a verified city-level finding. Too few, and the run searches again for the missing ones, up to the round limit." },
  "Knowledge indexing": { title: "Save", phase: "Publish", why: "Stores the verified findings so they can be searched, questioned and explored as a graph." },
  "Brief assembly": { title: "Build the brief", phase: "Publish", why: "Puts the findings, the open questions and the quality measures together." }
};
const guideFor = (step) => STEP_GUIDE[step.name] || { title: step.name, phase: "", why: "" };

function renderWorkflow(steps = []) {
  $("#step-count").textContent = `${steps.length} steps`;
  let phase = null;
  $("#workflow").innerHTML = steps.map((step, index) => {
    const guide = guideFor(step);
    const rounds = step.rounds?.length || 1;
    const heading = guide.phase && guide.phase !== phase ? `<div class="workflow-phase">${esc(guide.phase)}</div>` : "";
    phase = guide.phase || phase;
    return `${heading}
    <button class="workflow-step ${index === selectedStep ? "selected" : ""}" data-step-index="${index}" type="button">
      <div class="step-marker ${esc(step.status)}">${index + 1}</div>
      <div><strong>${esc(guide.title)}</strong><small class="step-technical">${esc(step.name)}</small><p>${esc(step.summary || step.detail)}</p></div>
      <span class="step-badges">${step.status === "warning" ? `<span class="step-state warning">Needs a look</span>` : ""}${rounds > 1 ? `<span class="step-state">${rounds} rounds</span>` : ""}</span>
    </button>`;
  }).join("");
  document.querySelectorAll(".workflow-step").forEach((button) => {
    button.addEventListener("click", () => {
      selectedStep = Number(button.dataset.stepIndex);
      renderWorkflow(researchResult.workflow);
      renderStepOutput();
    });
  });
}

const card = (label, value, tone = "") =>
  `<div class="output-card ${tone}"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;

function renderStepOutput() {
  if (!researchResult) return;
  const step = researchResult.workflow[selectedStep];
  const { sources = [], facts = [], gaps = [], metrics = {} } = researchResult;
  const approved = sources.filter((source) => source.crawl_allowed);
  const refused = sources.filter((source) => source.crawl_allowed === false);
  const rounds = step.rounds || [step];
  // Runs saved before per-round records existed only carry the last round's fields.
  const sum = (field) => rounds.reduce((total, item) => total + (Number(item[field]) || 0), 0);
  const recorded = (field) => rounds.some((item) => item[field] !== undefined);
  const authorityMix = {};
  rounds.forEach((item) => Object.entries(item.authority_mix || {}).forEach(([label, count]) => {
    authorityMix[label] = (authorityMix[label] || 0) + count;
  }));
  const threshold = step.threshold ?? 3;
  const dimensionsTotal = step.total ?? metrics.dimensions_total ?? 5;

  const views = {
    "Intake and guardrails": () => ({
      intro: `Research target resolved and ${researchResult.guardrails?.length || 0} guardrails declared before any network call.`,
      cards: [card("City", researchResult.city), card("Country", researchResult.country || "not supplied", researchResult.country ? "" : "caution"), card("Rounds run", researchResult.rounds)],
      body: `<p>The rules below were fixed at intake and applied for the whole run.</p>`
    }),
    "Research planning": () => ({
      intro: `Planned by ${esc(researchResult.planner)}.`,
      cards: [card("Searches", rounds.reduce((total, item) => total + (item.queries?.length || 0), 0)), card("Planner", researchResult.planner.split(":")[0]), card("Areas", dimensionsTotal)],
      body: rounds.some((item) => item.queries?.length)
        ? rounds.map((item) => `${rounds.length > 1 ? `<div class="chart-title">Round ${item.round}</div>` : ""}<div class="decision-list">${(item.queries || []).map((query) => `<div><span>${esc(query.query)}</span><strong>${esc(query.dimension)}</strong></div>`).join("")}</div>`).join("")
        : `<p class="empty">No queries recorded.</p>`
    }),
    "Live source discovery": () => ({
      intro: "Sources found on the public internet at request time, grouped by the kind of publisher.",
      cards: [card("Discovered", sources.length), ...Object.entries(authorityMix).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([label, count]) => card(label, count))],
      body: `<div class="source-list">${sources.slice(0, 8).map((source) => `<a href="${esc(source.url)}" target="_blank" rel="noreferrer"><strong>${esc(source.title)}</strong><span>${esc(source.discovered_via)}</span></a>`).join("")}</div>`
    }),
    "Crawlability gate": () => ({
      intro: "Every source is assessed against robots.txt before a single page is fetched.",
      cards: [card("Permitted", approved.length, "good"), card("Refused", refused.length, refused.length ? "caution" : ""), card("Gate runs", "before fetch")],
      body: `<div class="decision-list">${sources.slice(0, 10).map((source) => `<div><span class="decision-dot ${source.crawl_allowed ? "allowed" : "blocked"}"></span><span>${esc(source.title.slice(0, 70))}</span><strong>${source.crawl_allowed ? "allowed" : "refused"}</strong></div>`).join("")}</div>`
    }),
    "Evidence extraction": () => ({
      intro: "Permitted pages retrieved and reduced to clean text with a retrieval timestamp.",
      cards: [card("Pages read", metrics.sources_extracted || 0, "good"), card("Unreadable", gaps.filter((gap) => gap.kind === "unreachable").length, gaps.some((gap) => gap.kind === "unreachable") ? "caution" : ""), card("Reading limit", recorded("budget") ? `${sum("budget")} of ${approved.length} permitted` : (researchResult.profile?.max_sources ? `${researchResult.profile.max_sources} max` : "not recorded"))],
      body: `<p>Full text is kept only long enough to locate quotes; what persists is the quote, the URL and the timestamp.${recorded("waiting") && sum("waiting") ? ` ${sum("waiting")} permitted pages were left unread because of the depth you chose.` : ""}</p>`
    }),
    "Claim extraction": () => ({
      intro: "Specific statements proposed, each anchored to wording located in its source.",
      cards: [card("Candidates", metrics.candidate_claims || 0), card("Ungrounded dropped", sum("ungrounded"), sum("ungrounded") ? "caution" : "good"), card("Extractor", (step.extractor || "not recorded").split(":")[0])],
      body: `<p>A proposed quote that cannot be found in the source document is discarded before verification. That is the mechanical guard against a fabricated citation.</p>`
    }),
    "Independent fact check": () => ({
      intro: "A separate agent decides what may be published. It never wrote any of these claims.",
      cards: [card("Published", metrics.verified_facts || 0, "good"), card("Withheld", metrics.withheld_claims || 0, "caution"), card("Scope-flagged", metrics.scope_flagged_facts || 0, metrics.scope_flagged_facts ? "caution" : "")],
      body: `<p>Withheld claims become knowledge gaps. Claims whose evidence is national rather than city-level are published only with a scope warning attached.</p>`
    }),
    "Sufficiency review": () => ({
      intro: "Coverage is measured against the five research dimensions, then the run either publishes or plans another round.",
      cards: [card("Covered", `${metrics.dimensions_covered || 0} / ${dimensionsTotal}`, (metrics.dimensions_covered || 0) >= threshold ? "good" : "caution"), card("Needed to publish", `${threshold} areas`), card("Rounds", researchResult.rounds)],
      body: renderCoverageBars()
    }),
    "Knowledge indexing": () => ({
      intro: "The run is written to all three stores.",
      cards: Object.entries(researchResult.stores || {}).map(([name, store]) =>
        card(name, `${store.indexed_records ?? 0} records`, store.status === "ready" || store.status === "configured" ? "good" : "caution")),
      body: `<div class="output-body">${Object.values(researchResult.stores || {}).map((store) => `<p>${esc(store.detail || "")}</p>`).join("")}</div>`
    }),
    "Brief assembly": () => ({
      intro: "Findings, gaps and quality metrics assembled.",
      cards: [card("Status", researchResult.status), card("Findings", facts.length), card("Declared gaps", gaps.length, gaps.length ? "caution" : "good")],
      body: `<p>Unknowns stay visible instead of being filled with assumptions. The gaps are the questions to take into the meeting.</p>`
    })
  };

  const view = (views[step.name] || (() => ({ intro: step.detail, cards: [], body: "" })))();
  const guide = guideFor(step);
  const roundList = rounds.length > 1
    ? `<div class="round-list"><div class="chart-title">Round by round</div>${rounds.map((item) => `
        <div><span>Round ${esc(item.round)}</span><p>${esc(item.summary || item.detail)}</p></div>`).join("")}</div>`
    : "";
  const technical = `<details class="step-technical-detail"><summary>Technical detail</summary>${rounds.map((item) => `<p>${esc(item.detail)}</p>`).join("")}</details>`;
  $("#output-label").textContent = `Step ${selectedStep + 1} of ${researchResult.workflow.length}${guide.phase ? ` · ${guide.phase}` : ""}`;
  $("#step-output").innerHTML =
    `<h3 class="output-title">${esc(guide.title)}</h3>${guide.why ? `<p class="output-why">${esc(guide.why)}</p>` : ""}` +
    `<p class="output-intro">${esc(view.intro)}</p><div class="output-cards">${view.cards.join("")}</div><div class="output-body">${view.body}</div>${roundList}${technical}`;
  $("#guardrails").innerHTML = step.name === "Intake and guardrails" ? guardrailsHtml(researchResult.guardrails || []) : "";
}

function renderCoverageBars() {
  const dimensions = researchResult?.dimensions || [];
  if (!dimensions.length) return "";
  const max = Math.max(1, ...dimensions.map((dimension) => dimension.verified_facts));
  return `<div class="quality-chart"><div class="chart-title">City-level verified findings per dimension</div>${dimensions.map((dimension) => `
    <div class="bar-row"><span>${esc(dimension.label)}</span><div><i style="width:${(dimension.verified_facts / max) * 100}%"></i></div><strong>${dimension.verified_facts}</strong></div>`).join("")}</div>`;
}

const guardrailsHtml = (guardrails) => guardrails.length ? `
  <div class="guardrail-heading">Guardrails declared at intake</div>
  ${guardrails.map((item) => `
    <div class="guardrail-row">
      <span class="guardrail-icon ${esc(item.status)}">${item.status === "passed" ? "✓" : item.status === "warning" ? "!" : "×"}</span>
      <div><strong>${esc(item.name)}</strong><p>${esc(item.detail)}</p></div>
      <b>${esc(item.status)}</b>
    </div>`).join("")}` : "";

/* ------------------------------------------------------------------ */
/* Findings and gaps                                                   */
/* ------------------------------------------------------------------ */

function renderFacts(facts = []) {
  $("#fact-count").textContent = `${facts.length} findings`;
  $("#tab-fact-count").textContent = facts.length;

  const byDimension = new Map();
  facts.forEach((fact) => {
    const key = fact.dimension_label || fact.dimension || "Other";
    if (!byDimension.has(key)) byDimension.set(key, []);
    byDimension.get(key).push(fact);
  });

  $("#facts").innerHTML = facts.length ? [...byDimension.entries()].map(([label, items]) => `
    <h4 class="dimension-heading">${esc(label)} <span>${items.length}</span></h4>
    ${items.map((fact) => `
      <details class="fact ${fact.scope_flag ? "scope-warning" : ""}">
        <summary>
          <span class="fact-summary">
            <span class="tag">${esc(fact.geographic_scope)}</span>
            <strong>${esc(fact.claim)}</strong>
          </span>
          <span class="status-badge ${esc(fact.verification_status)}">${esc(friendly(fact.verification_status))}</span>
        </summary>
        <div class="fact-detail">
          ${fact.scope_flag ? `<p class="scope-alert">${esc(fact.scope_flag)}</p>` : ""}
          <blockquote>&ldquo;${esc(fact.evidence_quote)}&rdquo;</blockquote>
          ${fact.verification_note ? `<p class="verification-note">Checker: ${esc(fact.verification_note)}</p>` : ""}
          ${fact.entities?.length ? `<div class="entity-row">${fact.entities.map((entity) => `<span class="tag">${esc(entity.type)}: ${esc(entity.name)}</span>`).join("")}</div>` : ""}
          <p class="scope-note">Confidence ${fact.confidence} · checked by ${esc((fact.verified_by || "").split(":")[0])}</p>
          <a href="${esc(fact.source_url)}" target="_blank" rel="noreferrer">${esc(fact.source_title?.slice(0, 90) || "Open source")} ↗</a>
        </div>
      </details>`).join("")}`).join("")
    : `<p class="empty">No claim survived the independent evidence check, so this brief asserts nothing. The gaps opposite say why.</p>`;
}

function renderGaps(gaps = []) {
  const grouped = new Map();
  gaps.forEach((gap) => {
    const key = gap.kind || "missing";
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(gap);
  });
  $("#gaps").innerHTML = gaps.length ? [...grouped.entries()].map(([kind, items]) => `
    <h4 class="dimension-heading">${esc(KIND_LABEL[kind] || kind)} <span>${items.length}</span></h4>
    ${items.slice(0, 12).map((gap) => `
      <div class="gap">
        <div><strong>${esc(gap.topic)}</strong><p>${esc(gap.reason)}</p>
        ${gap.source_url ? `<a href="${esc(gap.source_url)}" target="_blank" rel="noreferrer">Open source ↗</a>` : ""}</div>
      </div>`).join("")}`).join("") : `<p class="empty">No gaps recorded.</p>`;
}

/* ------------------------------------------------------------------ */
/* Stores, metrics, trace                                              */
/* ------------------------------------------------------------------ */

function renderStores(stores) {
  $("#stores").innerHTML = Object.entries(stores || {}).map(([name, store]) => `
    <div class="store-row">
      <div><strong>${esc(store.provider)}</strong><span>${esc(store.role || name)}</span>
      ${store.detail ? `<span class="store-detail">${esc(store.detail)}</span>` : ""}</div>
      <b class="store-status ${esc(store.status)}">${esc(String(store.status).replaceAll("_", " "))}</b>
    </div>`).join("");
}

function renderObservability(result) {
  const metrics = result.metrics || {};
  const labels = {
    total_duration_ms: "Total duration", research_rounds: "Research rounds",
    sources_discovered: "Sources discovered", sources_crawl_approved: "Crawl permitted",
    sources_crawl_refused: "Crawl refused", sources_extracted: "Pages read",
    candidate_claims: "Candidate claims", verified_facts: "Verified findings",
    withheld_claims: "Withheld claims", scope_flagged_facts: "Scope-flagged",
    facts_checked_by_model: "Checked by model", facts_checked_by_rules: "Checked by rules",
    knowledge_gaps: "Knowledge gaps", dimensions_covered: "Dimensions covered",
    evidence_coverage_percent: "Evidence coverage", city_scope_quality_percent: "City-scope quality",
    mean_confidence: "Mean confidence", provider_errors: "Provider errors",
    guardrails_evaluated: "Guardrails", guardrail_warnings: "Guardrail warnings"
  };
  $("#metrics").innerHTML = Object.entries(labels).map(([key, label]) => {
    const value = metrics[key] ?? 0;
    const rendered = key.includes("percent") ? `${value}%` : key === "total_duration_ms" ? `${Math.round(value / 1000)}s` : value;
    return `<div class="metric-card"><span>${label}</span><strong>${esc(rendered)}</strong></div>`;
  }).join("");

  $("#quality-chart").innerHTML = `<div class="chart-title">Quality signal</div>${[
    ["Evidence coverage", metrics.evidence_coverage_percent || 0],
    ["City-scope quality", metrics.city_scope_quality_percent || 0],
    ["Dimension coverage", Math.round(((metrics.dimensions_covered || 0) / (metrics.dimensions_total || 5)) * 100)],
    ["Provider health", metrics.provider_errors ? 0 : 100]
  ].map(([label, value]) => `<div class="bar-row"><span>${label}</span><div><i style="width:${Math.min(100, value)}%"></i></div><strong>${value}%</strong></div>`).join("")}`;

  const trace = result.trace || [];
  $("#trace-count").textContent = `${trace.length} events`;
  $("#trace").innerHTML = trace.map((event) => `
    <div class="trace-row">
      <span class="trace-time">${esc(new Date(event.timestamp).toLocaleTimeString())}</span>
      <div><strong>${esc(event.stage)}</strong><p>${esc(event.detail)}</p></div>
      <b>${esc(Math.round(event.duration_ms))} ms</b>
    </div>`).join("") || `<p class="empty">No trace events.</p>`;

  $("#admin-guardrails").innerHTML = guardrailsHtml(result.guardrails || []);

  const summary = $("#admin-summary");
  summary.innerHTML = `<span class="summary-label">Run telemetry</span>
    <span>${Math.round((metrics.total_duration_ms || 0) / 1000)}s</span>
    <span>${metrics.research_rounds || 1} round(s)</span>
    <span>${metrics.verified_facts || 0} verified</span>
    <span>${metrics.withheld_claims || 0} withheld</span>
    <span>${metrics.provider_errors || 0} provider errors</span>
    <button type="button" data-open-runtime>Runtime details ↗</button>`;
  summary.classList.remove("hidden");
  summary.querySelector("[data-open-runtime]").addEventListener("click", () => $('[data-tab="runtime"]').click());
}

/* ------------------------------------------------------------------ */
/* Graph, history, workflow structure                                  */
/* ------------------------------------------------------------------ */

let graphPollTimer = null;

function graphBuildHtml(build) {
  if (!build || !build.status || build.status === "unknown") return "";
  const building = build.status === "queued" || build.status === "running";
  const done = build.episodes_written || 0;
  const total = build.episodes_total || 0;
  const percent = total ? Math.round((done / total) * 100) : 0;
  const label = {
    queued: "Queued", running: "Building the knowledge graph", completed: "Graph built", ready: "Graph built",
    partial: "Graph partially built", failed: "Graph build failed", skipped: "Graph build skipped", superseded: "Superseded by a newer run"
  }[build.status] || build.status;
  return `<div class="search-progress graph-build">
    <div class="progress-top"><span>${esc(label)}</span><strong>${done} / ${total} episodes</strong></div>
    <div class="progress-track"><span style="width:${building ? Math.max(percent, 4) : percent}%"></span></div>
    <p>${building
      ? "The brief is ready now. Relationships between organisations, programmes and policies are being extracted in the background; this view refreshes itself."
      : esc(build.detail || (build.errors || []).join(" · ") || "")}</p>
  </div>`;
}

async function loadGraph(city) {
  const view = $("#graph-view");
  clearTimeout(graphPollTimer);
  try {
    const graph = await getJSON(`/api/graph/${encodeURIComponent(city)}`);
    const nodes = graph.nodes || [];
    const ledger = graph.entities_from_ledger || [];
    const build = graph.build || {};
    $("#graph-count").textContent = `${nodes.length} graph entities`;
    if (build.status === "queued" || build.status === "running") {
      graphPollTimer = setTimeout(() => loadGraph(city), 10000);
    }

    if (!nodes.length) {
      view.innerHTML = `${graphBuildHtml(build)}
        <p class="empty">${esc(graph.detail || "The knowledge graph holds no relationships for this city yet.")}</p>
        ${ledger.length ? `<div class="panel-intro">Entities recorded in the relational ledger during this run:</div>
          <div class="graph-nodes">${ledger.map((entity) => `<div class="graph-node"><span>${esc(entity.type)}</span><strong>${esc(entity.name)}</strong><small>${entity.mentions} mention(s)</small></div>`).join("")}</div>` : ""}`;
      return;
    }
    const labels = new Map(nodes.map((node) => [node.id, node.label]));
    view.innerHTML = `${graphBuildHtml(build)}
      <div class="graph-nodes">${nodes.map((node) => `<div class="graph-node"><span>${esc(node.type)}</span><strong>${esc(node.label)}</strong>${node.summary ? `<small>${esc(node.summary)}</small>` : ""}</div>`).join("")}</div>
      <div class="graph-edges">${(graph.edges || []).map((edge) => `
        <div class="graph-edge"><strong>${esc(labels.get(edge.source) || "Entity")}</strong><span>→ ${esc(edge.label)} →</span><strong>${esc(labels.get(edge.target) || "Entity")}</strong>
        ${edge.fact ? `<em>${esc(edge.fact)}</em>` : ""}</div>`).join("")}</div>`;
  } catch (error) {
    view.innerHTML = `<p class="error">Could not load the knowledge graph: ${esc(error.message)}</p>`;
  }
}

function renderWorkflowStructure(structure) {
  const target = $("#workflow-structure");
  if (!target || !structure) return;
  const nodes = structure.nodes || [];
  const outgoing = new Map();
  (structure.edges || []).forEach((edge) => {
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge);
  });
  target.innerHTML = `
    <p class="panel-intro">Read directly from the compiled ${esc(structure.framework)}, so it cannot drift from the code that runs.</p>
    <div class="graph-nodes">${nodes.map((node) => `
      <div class="graph-node ${node.is_gate ? "gate" : ""}">
        <span>${node.is_gate ? "gate" : "stage"}</span>
        <strong>${esc(node.name)}</strong>
        <small>${esc(node.responsibility)}</small>
        <em>→ ${esc((outgoing.get(node.id) || []).map((edge) => edge.target + (edge.conditional ? " (conditional)" : "")).join(", ") || "end")}</em>
      </div>`).join("")}</div>
    <div class="callout-inline"><strong>Cycle:</strong> ${esc(structure.cycle?.from)} → ${esc(structure.cycle?.to)} when ${esc(structure.cycle?.condition)}. Bounded by ${esc(structure.cycle?.bounded_by)}.</div>`;
}

function renderHistory(runs) {
  $("#history").innerHTML = runs.length ? runs.map((run) => `
    <div class="history-row">
      <div><strong>${esc(run.city)}${run.country ? `, ${esc(run.country)}` : ""}</strong>
      <span>${esc(new Date(run.completed_at).toLocaleString())} · ${esc(run.status)}</span></div>
      <div class="history-metrics">
        <span>${run.facts} findings</span><span>${run.gaps} gaps</span>
        <span>${run.dimensions_covered}/5 dimensions</span>
        <span class="depth-tag">${esc(run.depth || "balanced")}</span>
        <a href="${API_BASE}/api/report/${encodeURIComponent(run.city)}" target="_blank">Brief ↓</a>
      </div>
    </div>`).join("") : `<p class="empty">No completed research runs yet.</p>`;
}

/* ------------------------------------------------------------------ */
/* Ask                                                                 */
/* ------------------------------------------------------------------ */

function citationHtml(evidence, index) {
  return `<blockquote data-cite="${index + 1}">
    <span class="cite-index">[${index + 1}]</span>
    <span class="cite-origin">${esc(evidence.origin || "ledger")}</span>
    ${evidence.scope_flag ? `<span class="scope-alert">${esc(evidence.scope_flag)}</span>` : ""}
    &ldquo;${esc(evidence.evidence_quote)}&rdquo;
    ${evidence.source_url ? `<a href="${esc(evidence.source_url)}" target="_blank" rel="noreferrer">${esc(evidence.source_title?.slice(0, 80) || "Source")} ↗</a>` : `<em>${esc(evidence.source_title || "Knowledge graph")}</em>`}
  </blockquote>`;
}

async function askQuestion(event) {
  event.preventDefault();
  if (!researchResult) return;
  const answer = $("#answer");
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  answer.innerHTML = `<p class="empty">Searching the vector store, the knowledge graph and the ledger…</p>`;
  try {
    const response = await fetch(`${API_BASE}/api/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ city: researchResult.city, question: $("#question").value.trim() })
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Question failed");
    const result = await response.json();
    const retrieval = result.retrieval || {};
    answer.innerHTML = `
      <p class="answer-text ${result.sufficient ? "" : "insufficient"}">${esc(result.answer)}</p>
      <div class="answer-meta">
        <span>${esc(result.confidence)} confidence</span>
        <span>${result.cited?.length || 0} cited</span>
        <span>vector ${retrieval.vector_hits ?? 0} · graph ${retrieval.graph_hits ?? 0}</span>
        <span>${esc((result.synthesiser || "").split(":")[0])}</span>
      </div>
      ${result.caveat ? `<p class="answer-caveat">${esc(result.caveat)}</p>` : ""}
      ${result.evidence?.length ? `<div class="answer-evidence">${result.evidence.map(citationHtml).join("")}</div>` : ""}
      ${result.related_gaps?.length ? `<div class="related-gaps"><div class="chart-title">Related knowledge gaps</div>${result.related_gaps.map((gap) => `<p>${esc(gap.topic)}: ${esc(gap.reason)}</p>`).join("")}</div>` : ""}`;
  } catch (error) {
    answer.innerHTML = `<p class="error">${esc(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------ */
/* Run                                                                 */
/* ------------------------------------------------------------------ */

async function pollProgress(city) {
  try {
    const progress = await getJSON(`/api/progress/${encodeURIComponent(city)}`);
    $("#progress-stage").textContent = progress.stage;
    $("#progress-percent").textContent = `${progress.percent}%`;
    $("#progress-bar").style.width = `${progress.percent}%`;
    $("#progress-message").textContent = progress.message;
  } catch (error) { /* progress is best-effort */ }
}

$("#research-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const city = $("#city").value.trim();
  const country = $("#country").value.trim();
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  button.querySelector("span").textContent = "Researching…";
  $("#error").textContent = "";
  $("#search-progress").classList.remove("hidden");
  $("#workspace-title").textContent = `Researching ${city}`;
  $("#run-status").textContent = "Live run";
  $("#workflow").innerHTML = `<p class="empty">Contacting public sources…</p>`;

  const timer = setInterval(() => pollProgress(city), 900);
  pollProgress(city);
  try {
    const response = await fetch(`${API_BASE}/api/research`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ city, country: country || null, depth: selectedDepth() })
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Research could not be started");

    // The run continues on the server. Poll for its outcome; a failed poll is a
    // network blip, not a failed run, so keep going rather than giving up.
    const started = Date.now();
    let misses = 0;
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 2500));
      let job;
      try {
        job = await getJSON(`/api/research/${encodeURIComponent(city)}`);
        misses = 0;
      } catch (pollError) {
        misses += 1;
        $("#progress-message").textContent = "Connection interrupted — the research is still running on the server. Reconnecting…";
        if (misses > 120) throw new Error("Lost contact with the server. The run may still finish; check History shortly.");
        continue;
      }
      if (job.status === "completed") break;
      if (job.status === "failed") throw new Error(job.error || "Research failed");
      if (Date.now() - started > 30 * 60 * 1000) throw new Error("Research is taking unusually long. Check History shortly.");
    }
    researchResult = await getJSON(`/api/run/${encodeURIComponent(city)}`);
    selectedStep = 0;

    $("#workspace").classList.remove("pre-run");
    $('[data-tab="overview"]').click();
    $("#workspace-title").textContent = `${researchResult.city} city brief`;
    $("#run-status").textContent = researchResult.status.replaceAll("_", " ");
    const reportLink = $("#download-report");
    reportLink.href = `${API_BASE}/api/report/${encodeURIComponent(researchResult.city)}`;
    reportLink.classList.remove("hidden");

    renderWorkflow(researchResult.workflow);
    renderStepOutput();
    renderFacts(researchResult.facts);
    renderGaps(researchResult.gaps);
    renderStores(researchResult.stores);
    renderObservability(researchResult);
    loadGraph(researchResult.city);
    loadHistory();
    getJSON("/api/runtime").then((runtime) => { renderRuntimeBanner(runtime); renderRuntimeDetail(runtime); }).catch(() => {});
    await pollProgress(city);
  } catch (error) {
    $("#error").textContent = error.message;
    $("#run-status").textContent = "Needs attention";
  } finally {
    clearInterval(timer);
    button.disabled = false;
    button.querySelector("span").textContent = "Start research";
  }
});

const selectedDepth = () => document.querySelector('input[name="depth"]:checked')?.value || "balanced";

function renderDepthOptions(config) {
  const target = $("#depth-options");
  if (!target || !config?.profiles?.length) return;
  const legend = target.querySelector("legend").outerHTML;
  target.innerHTML = legend + config.profiles.map((profile) => `
    <label class="depth-option" title="${esc(profile.summary)}">
      <input type="radio" name="depth" value="${esc(profile.key)}" ${profile.key === config.default ? "checked" : ""} />
      <span>
        <strong>${esc(profile.label)}</strong>
        <em>${esc(profile.estimate)}</em>
        <small>${esc(profile.summary)}</small>
      </span>
    </label>`).join("");
}

const loadHistory = () => getJSON("/api/history").then(renderHistory).catch(() => {});

$("#ask-form").addEventListener("submit", askQuestion);

document.querySelectorAll(".workspace-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".workspace-tab").forEach((item) => item.classList.toggle("active", item === tab));
    document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === tab.dataset.tab));
  });
});

async function checkVersion() {
  // A page from one release talking to a server from another is how a cached
  // script produced a cryptic error. Say what is wrong and how to fix it.
  try {
    const health = await getJSON("/health");
    if (health.version && health.version !== APP_VERSION) {
      const banner = $("#version-banner");
      banner.innerHTML = `<strong>A newer version is available</strong>
        <p>This page is version ${esc(APP_VERSION)} but the server is ${esc(health.version)}. Reload to get the latest version before starting research.</p>
        <button type="button" id="reload-app">Reload now</button>`;
      banner.classList.remove("hidden");
      $("#reload-app").addEventListener("click", () => window.location.reload());
    }
  } catch (error) { /* health is best-effort */ }
}

checkVersion();
loadHistory();
getJSON("/api/profiles").then(renderDepthOptions).catch(() => {});
getJSON("/api/stores").then(renderStores).catch(() => {});
getJSON("/api/runtime").then((runtime) => { renderRuntimeBanner(runtime); renderRuntimeDetail(runtime); }).catch(() => {});
getJSON("/api/workflow").then((structure) => { workflowStructure = structure; renderWorkflowStructure(structure); }).catch(() => {});
