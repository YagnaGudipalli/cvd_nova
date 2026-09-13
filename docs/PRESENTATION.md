# Demonstration deck

The deck is an interactive 8-slide page served at
[`/presentation.html`](../frontend/presentation.html).

- Arrow keys, PageUp/PageDown, Home/End to navigate.
- **Save as PDF** button prints one 16:9 slide per page.

## Slides

1. **Title** — understand a city before the first meeting.
2. **The problem** — public knowledge is everywhere, confidence is not. The
   failure mode is being confidently wrong in the room.
3. **Problem decomposition** — the five dimensions "understanding a city" was
   defined as, and the deliberate exclusion of named individuals.
4. **Agentic design** — no agent both produces a claim and approves it; the two
   gates and the consequence of a refusal.
5. **Data architecture** — three stores, three different questions.
6. **Trust and evidence** — no quote, no fact; scope flagging; unknowns as output.
7. **User experience** — the six-step path a City Lead actually walks.
8. **Trade-offs and limitations** — what was cut, what is still shallow, what next.

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
