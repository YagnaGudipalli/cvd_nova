# Demonstration deck

The deck is an interactive 11-slide page served at
[`/presentation.html`](../frontend/presentation.html).

- Arrow keys, PageUp/PageDown, Home/End to navigate.
- **Save as PDF** button prints one 16:9 slide per page.

## Slides

Each content slide is headed by its section title and written as short pointers,
tables and simple charts. Numbers come from the recorded Nairobi run in
[`examples/nairobi-research-run.json`](examples/nairobi-research-run.json).

1. **Title** — who it is for, input and output, four headline numbers.
2. **The problem, and how it was decomposed** — the confidently-wrong risk; the five
   dimensions as a table with an example question each.
3. **Worked example: Nairobi, Kenya** — source-to-fact funnel, facts per dimension,
   one finding, one gap, and the gap breakdown.
4. **AI solution and architecture** — the ten-node flow and an agent table with what
   each did in the Nairobi run.
5. **Tech stack** — layer, choice and reason.
6. **Knowledge management and retrieval** — three stores, the question pipeline.
7. **Trustworthiness and evidence** — guard table, a passing and an illustrative
   failing claim.
8. **Evaluation** — run metrics, where the time went, and an honest reading (that run
   was rules-verified with no LLM available; grounding is not relevance).
9. **Engineering decisions** — decisions with measured effect.
10. **User experience** — seven-step journey, depth options, workspace tabs.
11. **Trade-offs and limitations** — current state, impact, next step; demo path.

Only the Quick depth time (42 s) is measured; Balanced and Thorough are estimates and
are labelled as such on slide 10.

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
