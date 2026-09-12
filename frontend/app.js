const form = document.querySelector("#research-form");
const workspace = document.querySelector("#workspace");
const error = document.querySelector("#error");
let researchResult = null;
let selectedStep = 0;

const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[character]));

function friendlyStatus(status) {
  const labels = {
    PARTIALLY_SUPPORTED: "Partially supported",
    SUPPORTED: "Supported",
    INSUFFICIENT_EVIDENCE: "Needs more evidence",
    NOT_CONFIGURED: "Not configured"
  };
  return labels[status] || String(status).replaceAll("_", " ").toLowerCase();
}

function renderWorkflow(steps) {
  document.querySelector("#step-count").textContent = `${steps.length} steps`;
  document.querySelector("#workflow").innerHTML = steps.map((step, index) => `
    <button class="workflow-step ${index === selectedStep ? "selected" : ""}" data-step-index="${index}" type="button" aria-pressed="${index === selectedStep}">
      <div class="step-marker ${step.status}">${step.status === "completed" ? "✓" : index + 1}</div>
      <div><strong>${escapeHtml(step.name)}</strong><p>${escapeHtml(step.detail)}</p></div>
      <span class="step-state">${escapeHtml(step.status)}</span>
    </button>
  `).join("");
  document.querySelectorAll(".workflow-step").forEach((stepButton) => {
    stepButton.addEventListener("click", () => {
      selectedStep = Number(stepButton.dataset.stepIndex);
      renderWorkflow(researchResult.workflow);
      renderStepOutput();
    });
  });
}

function outputCard(label, value, tone = "") {
  return `<div class="output-card ${tone}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderStepOutput() {
  if (!researchResult) return;
  const step = researchResult.workflow[selectedStep];
  const sources = researchResult.sources || [];
  const facts = researchResult.facts || [];
  const gaps = researchResult.gaps || [];
  const approved = sources.filter((source) => source.crawl_allowed);
  const rejected = sources.filter((source) => source.crawl_allowed === false);
  const outputs = {
    "City disambiguation": {
      intro: "Resolved research target",
      cards: [outputCard("City", researchResult.city), outputCard("Country", researchResult.country || "Not supplied", !researchResult.country ? "caution" : ""), outputCard("Scope", "City-level research")],
      body: `<p>The workflow will keep national and regional evidence separate from this target city.</p>`
    },
    "Research planning": {
      intro: "Research plan generated",
      cards: [outputCard("Topics", "3 research areas"), outputCard("Mode", "Live internet research"), outputCard("Reuse", "Run is timestamped")],
      body: `<ul class="output-list"><li>Cardiovascular health and risk factors</li><li>Healthcare access and policy</li><li>Prevention strategies and programs</li></ul>`
    },
    "Live web discovery": {
      intro: "Candidate sources returned at request time",
      cards: [outputCard("Candidates", sources.length), outputCard("Unique URLs", new Set(sources.map((source) => source.url)).size), outputCard("Status", "Collected", "good")],
      body: sources.length ? `<div class="source-list">${sources.slice(0, 6).map((source) => `<a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer"><strong>${escapeHtml(source.title)}</strong><span>${escapeHtml(source.url)}</span></a>`).join("")}</div>` : `<p class="empty">No search results returned.</p>`
    },
    "Crawlability detection": {
      intro: "Every source is assessed before extraction",
      cards: [outputCard("Approved", approved.length, "good"), outputCard("Blocked", rejected.length, rejected.length ? "caution" : ""), outputCard("Gate", "Before crawl")],
      body: `<div class="decision-list">${sources.slice(0, 8).map((source) => `<div><span class="decision-dot ${source.crawl_allowed ? "allowed" : "blocked"}"></span><span>${escapeHtml(source.title)}</span><strong>${source.crawl_allowed ? "allowed" : "blocked"}</strong></div>`).join("") || '<p class="empty">No sources assessed.</p>'}</div>`
    },
    "Evidence extraction": {
      intro: "Readable page content prepared for evidence review",
      cards: [outputCard("Pages read", Math.min(5, approved.length)), outputCard("Evidence ready", facts.length, facts.length ? "good" : "caution"), outputCard("Missing", gaps.filter((gap) => gap.topic === "source extraction").length, "caution")],
      body: `<p>Page text is cleaned and retained with its URL and retrieval timestamp before a fact can be proposed.</p>`
    },
    "Independent fact checking": {
      intro: "A separate validation gate decides what can be published",
      cards: [outputCard("Candidates", facts.length + gaps.filter((gap) => gap.topic === "source_discovery").length), outputCard("Published", facts.length, facts.length ? "good" : "caution"), outputCard("Withheld", gaps.filter((gap) => gap.source_url).length, "caution")],
      body: `<p>${facts.length ? "Published facts passed the evidence gate." : "No claim passed the city-level evidence gate, so the report makes no unsupported assertion."}</p>`
    },
    "Knowledge graph and stores": {
      intro: "The run is recorded for future institutional memory",
      cards: [outputCard("Relational ledger", researchResult.stores?.relational?.status || "unknown", "good"), outputCard("Vector store", researchResult.stores?.vector?.status || "unknown", researchResult.stores?.vector?.status === "configured" ? "good" : "caution"), outputCard("Knowledge graph", researchResult.stores?.graph?.status || "unknown", researchResult.stores?.graph?.status === "configured" ? "good" : "caution")],
      body: `<p>Sources and verified facts are persisted in the relational ledger. Provider readiness is reported explicitly so a deployment never implies that Qdrant or Neo4j was used when it was not configured.</p>`
    },
    "Report generation": {
      intro: "Evidence-first brief assembled",
      cards: [outputCard("Run status", researchResult.status), outputCard("Findings", facts.length), outputCard("Knowledge gaps", gaps.length, gaps.length ? "caution" : "good")],
      body: `<p>The report is ready to explore. Unknowns remain visible instead of being filled with assumptions.</p>`
    }
  };
  const output = outputs[step.name] || { intro: step.detail, cards: [], body: "" };
  document.querySelector("#output-label").textContent = `${selectedStep + 1} / ${researchResult.workflow.length}`;
  document.querySelector("#step-output").innerHTML = `<p class="output-intro">${escapeHtml(output.intro)}</p><div class="output-cards">${output.cards.join("")}</div><div class="output-body">${output.body}</div>`;
  if (step.name === "City disambiguation") renderGuardrails(researchResult.guardrails || [], "#guardrails");
  else document.querySelector("#guardrails").innerHTML = "";
}

function renderGuardrails(guardrails, selector) {
  const target = document.querySelector(selector);
  if (!target) return;
  target.innerHTML = guardrails.length ? `<div class="guardrail-heading">Guardrails evaluated at intake</div>${guardrails.map((item) => `<div class="guardrail-row"><span class="guardrail-icon ${escapeHtml(item.status)}">${item.status === "passed" ? "✓" : item.status === "warning" ? "!" : "×"}</span><div><strong>${escapeHtml(item.name)}</strong><p>${escapeHtml(item.detail)}</p></div><b>${escapeHtml(item.status)}</b></div>`).join("")}` : "";
}

function renderFacts(facts) {
  document.querySelector("#fact-count").textContent = `${facts.length} findings`;
  document.querySelector("#tab-fact-count").textContent = facts.length;
  document.querySelector("#facts").innerHTML = facts.length ? facts.map((fact) => `
    <details class="fact">
      <summary><span class="fact-summary"><span class="tag">Source evidence</span><strong>${escapeHtml(fact.claim)}</strong></span><span class="status-badge ${escapeHtml(fact.verification_status)}">${escapeHtml(friendlyStatus(fact.verification_status))}</span></summary>
      <div class="fact-detail"><p class="scope-note">${escapeHtml(fact.geographic_scope || "Scope not specified")}</p><blockquote>“${escapeHtml(fact.evidence_quote)}”</blockquote>${fact.verification_note ? `<p class="verification-note">${escapeHtml(fact.verification_note)}</p>` : ""}<a href="${escapeHtml(fact.source_url)}" target="_blank" rel="noreferrer">Open source ↗</a></div>
    </details>
  `).join("") : '<p class="empty">No evidence-backed facts were published.</p>';
}

function renderGaps(gaps) {
  document.querySelector("#gaps").innerHTML = gaps.map((gap) => `
    <div class="gap"><span>!</span><div><strong>${escapeHtml(gap.topic)}</strong><p>${escapeHtml(gap.reason)}</p></div></div>
  `).join("");
}

function renderStores(stores) {
  document.querySelector("#stores").innerHTML = Object.entries(stores || {}).map(([name, store]) => `
    <div class="store-row"><div><strong>${escapeHtml(store.provider)}</strong><span>${escapeHtml(name)} store</span></div><b class="store-status ${escapeHtml(store.status)}">${escapeHtml(store.status.replaceAll("_", " "))}</b></div>
  `).join("");
}

function renderAdminObservability(result) {
  const metrics = result.metrics || {};
  const labels = {
    total_duration_ms: "Total duration",
    sources_discovered: "Sources discovered",
    sources_crawl_approved: "Sources approved",
    sources_extracted: "Sources extracted",
    candidate_facts: "Candidate facts",
    verified_facts: "Verified facts",
    withheld_facts: "Withheld facts",
    knowledge_gaps: "Knowledge gaps",
    evidence_coverage_percent: "Evidence coverage",
    city_scope_quality_percent: "City-scope quality",
    provider_errors: "Provider errors",
    guardrails_evaluated: "Guardrails evaluated",
    guardrails_passed: "Guardrails passed",
    guardrail_warnings: "Guardrail warnings",
    guardrails_blocked: "Guardrails blocked"
  };
  document.querySelector("#metrics").innerHTML = Object.entries(labels).map(([key, label]) => {
    const value = metrics[key] ?? 0;
    const rendered = key.includes("percent") ? `${value}%` : key === "total_duration_ms" ? `${value} ms` : value;
    return `<div class="metric-card"><span>${label}</span><strong>${escapeHtml(rendered)}</strong></div>`;
  }).join("");
  document.querySelector("#quality-chart").innerHTML = `<div class="chart-title">Quality signal</div>${[["Evidence coverage", metrics.evidence_coverage_percent || 0], ["City-scope quality", metrics.city_scope_quality_percent || 0], ["Provider health", metrics.provider_errors ? 0 : 100]].map(([label, value]) => `<div class="bar-row"><span>${label}</span><div><i style="width:${Math.min(100, value)}%"></i></div><strong>${value}%</strong></div>`).join("")}`;
  const trace = result.trace || [];
  document.querySelector("#trace-count").textContent = `${trace.length} events`;
  document.querySelector("#trace").innerHTML = trace.length ? trace.map((event) => `
    <div class="trace-row"><span class="trace-time">${escapeHtml(new Date(event.timestamp).toLocaleTimeString())}</span><div><strong>${escapeHtml(event.stage)}</strong><p>${escapeHtml(event.detail)}</p></div><b>${escapeHtml(event.duration_ms)} ms</b></div>
  `).join("") : '<p class="empty">No trace events recorded.</p>';
  renderGuardrails(result.guardrails || [], "#admin-guardrails");
  const summary = document.querySelector("#admin-summary");
  summary.innerHTML = `<span class="summary-label">Admin telemetry</span><span>${escapeHtml(metrics.total_duration_ms ?? 0)} ms</span><span>${escapeHtml(metrics.guardrails_evaluated ?? 0)} guardrails</span><span>${escapeHtml(metrics.verified_facts ?? 0)} verified facts</span><span>${escapeHtml(metrics.provider_errors ?? 0)} provider errors</span><button type="button" data-open-runtime>View runtime details ↗</button>`;
  summary.classList.remove("hidden");
  summary.querySelector("[data-open-runtime]").addEventListener("click", () => document.querySelector('[data-tab="runtime"]').click());
}

function renderHistory(runs) {
  document.querySelector("#history").innerHTML = runs.length ? runs.map((run) => `
    <div class="history-row"><div><strong>${escapeHtml(run.city)}${run.country ? `, ${escapeHtml(run.country)}` : ""}</strong><span>${escapeHtml(new Date(run.completed_at).toLocaleString())}</span></div><div class="history-metrics"><span>${run.facts} findings</span><span>${run.gaps} gaps</span><a href="/api/report/${encodeURIComponent(run.city)}" target="_blank">Brief ↓</a></div></div>
  `).join("") : '<p class="empty">No completed research runs yet.</p>';
}

async function loadHistory() {
  const response = await fetch("/api/history");
  if (response.ok) renderHistory(await response.json());
}

async function loadRuntimeStatus() {
  const response = await fetch("/api/stores");
  if (response.ok) renderStores(await response.json());
}

async function pollProgress(city) {
  const response = await fetch(`/api/progress/${encodeURIComponent(city)}`);
  if (!response.ok) return;
  const progress = await response.json();
  document.querySelector("#progress-stage").textContent = progress.stage;
  document.querySelector("#progress-percent").textContent = `${progress.percent}%`;
  document.querySelector("#progress-bar").style.width = `${progress.percent}%`;
  document.querySelector("#progress-message").textContent = progress.message;
}

async function loadGraph(city) {
  const view = document.querySelector("#graph-view");
  try {
    const response = await fetch(`/api/graph/${encodeURIComponent(city)}`);
    const graph = await response.json();
    document.querySelector("#graph-count").textContent = `${graph.nodes.length} entities`;
    if (!graph.nodes.length) {
      view.innerHTML = '<p class="empty">No graph relationships were returned for this city yet.</p>';
      return;
    }
    const labels = new Map(graph.nodes.map((node) => [node.id, node.label]));
    view.innerHTML = `<div class="graph-nodes">${graph.nodes.map((node) => `<div class="graph-node"><span>${escapeHtml(node.type)}</span><strong>${escapeHtml(node.label)}</strong></div>`).join("")}</div><div class="graph-edges">${graph.edges.map((edge) => `<div class="graph-edge"><strong>${escapeHtml(labels.get(edge.source) || "Entity")}</strong><span>→ ${escapeHtml(edge.label)} →</span><strong>${escapeHtml(labels.get(edge.target) || "Entity")}</strong></div>`).join("")}</div>`;
  } catch (requestError) {
    view.innerHTML = `<p class="error">Could not load the knowledge graph: ${escapeHtml(requestError.message)}</p>`;
  }
}

async function askQuestion(event) {
  event.preventDefault();
  if (!researchResult) return;
  const questionInput = document.querySelector("#question");
  const answer = document.querySelector("#answer");
  const askButton = event.currentTarget.querySelector("button");
  askButton.disabled = true;
  answer.innerHTML = '<p class="empty">Checking the verified ledger...</p>';
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ city: researchResult.city, question: questionInput.value.trim() })
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Question failed");
    const result = await response.json();
    answer.innerHTML = `<p class="answer-text">${escapeHtml(result.answer)}</p><div class="answer-meta"><span>${escapeHtml(result.confidence)} confidence</span><span>${result.evidence.length} cited facts</span></div>${result.evidence.length ? `<div class="answer-evidence">${result.evidence.map((fact) => `<blockquote>“${escapeHtml(fact.evidence_quote)}”<a href="${escapeHtml(fact.source_url)}" target="_blank" rel="noreferrer">Source ↗</a></blockquote>`).join("")}</div>` : `<p class="answer-caveat">${escapeHtml(result.caveat)}</p>`}`;
  } catch (requestError) {
    answer.innerHTML = `<p class="error">${escapeHtml(requestError.message)}</p>`;
  } finally {
    askButton.disabled = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const city = document.querySelector("#city").value.trim();
  const country = document.querySelector("#country").value.trim();
  const button = form.querySelector("button");
  button.disabled = true;
  button.querySelector("span").textContent = "Researching...";
  error.textContent = "";
  workspace.classList.remove("hidden");
  const progressPanel = document.querySelector("#search-progress");
  progressPanel.classList.remove("hidden");
  const progressTimer = setInterval(() => pollProgress(city), 700);
  pollProgress(city);
  document.querySelector("#workspace-title").textContent = `Researching ${city}`;
  document.querySelector("#run-status").textContent = "Live run";
  document.querySelector("#workflow").innerHTML = '<p class="empty">Contacting public sources...</p>';
  try {
    const response = await fetch("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ city, country: country || null })
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Research failed");
    const result = await response.json();
    researchResult = result;
    selectedStep = 0;
    workspace.classList.remove("pre-run");
    const overviewTab = document.querySelector('[data-tab="overview"]');
    document.querySelectorAll(".workspace-tab").forEach((item) => item.classList.toggle("active", item === overviewTab));
    document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === "overview"));
    document.querySelector("#workspace-title").textContent = `${result.city} intelligence brief`;
    document.querySelector("#run-status").textContent = "Complete with gaps";
    const reportLink = document.querySelector("#download-report");
    reportLink.href = `/api/report/${encodeURIComponent(result.city)}`;
    reportLink.classList.remove("hidden");
    renderWorkflow(result.workflow);
    renderStepOutput();
    renderFacts(result.facts);
    renderGaps(result.gaps);
    renderStores(result.stores);
    renderAdminObservability(result);
    loadGraph(result.city);
    loadHistory();
    await pollProgress(city);
  } catch (requestError) {
    error.textContent = requestError.message;
    document.querySelector("#run-status").textContent = "Needs attention";
  } finally {
    clearInterval(progressTimer);
    button.disabled = false;
    button.querySelector("span").textContent = "Start research";
  }
});

document.querySelector("#ask-form").addEventListener("submit", askQuestion);
loadHistory();
loadRuntimeStatus();

document.querySelectorAll(".workspace-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.dataset.tab;
    document.querySelectorAll(".workspace-tab").forEach((item) => item.classList.toggle("active", item === tab));
    document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === target));
  });
});