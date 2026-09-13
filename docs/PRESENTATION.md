# Demonstration deck

The deck is an interactive 8-slide page served at
[`/presentation.html`](../frontend/presentation.html).

- Arrow keys, PageUp/PageDown, Home/End to navigate.
- **Save as PDF** button prints one 16:9 slide per page.

## Slides

Eight slides, following the case study's brief for the deck: the problem as
understood, the AI solution and architecture, trust and evidence, the user
experience, and trade-offs and limitations.

1. **Title** — understand a city before the first meeting; four headline numbers.
2. **Problem and decomposition** — the failure mode is being confidently wrong in the
   room; the five dimensions and the exclusion of named individuals.
3. **Architecture and agents** — ten LangGraph nodes, three gates, the bounded
   sufficiency cycle, and a checker that is never the extracting model.
4. **Knowledge management and retrieval** — three stores for three questions,
   concurrent vector and graph retrieval, cited or declined answers.
5. **Trust and evidence** — quote grounding, numeric guard, scope flagging, the gap
   ledger, and degraded runs that say so.
6. **Engineering decisions** — one table of choices, each with its measured evidence:
   model per job, deterministic checks first, strict schemas, rate-limit handling,
   background graph build, sizing to the free host.
7. **User experience** — depth choice with measured and estimated times, and the path
   a City Lead walks.
8. **Trade-offs and limitations** — what was cut, what free tiers cost, what is not
   modelled, what comes next, and the demo path.

Only the Quick depth time (42 s) is measured; Balanced and Thorough are estimates and
are labelled as such on slide 7.

## Demo path

1. Enter a city the audience picks.
2. Walk the workflow trace stage by stage while it runs.
3. Open the **Workflow** tab — the compiled LangGraph structure, including the
   sufficiency cycle.
4. Open a finding to its verbatim quote and source; show a scope-flagged one.
5. Read the gap ledger — what the system refused to claim, and why.
6. Open the **Knowledge graph** tab.
7. Ask a question; show the citations and the vector/graph retrieval counts.
8. Download the brief.
9. Open **Admin / runtime** for quality metrics and active capability.
