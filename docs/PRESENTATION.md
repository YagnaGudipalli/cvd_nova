# Demonstration deck

The deck is an interactive 11-slide page served at
[`/presentation.html`](../frontend/presentation.html).

- Arrow keys, PageUp/PageDown, Home/End to navigate.
- **Save as PDF** button prints one 16:9 slide per page.

## Slides

Each slide is headed by its section title and kept to short pointers, one table or
one chart. Numbers come from the recorded Hyderabad run in
[`examples/hyderabad-research-run.json`](examples/hyderabad-research-run.json)
(Balanced depth, one round, 51 s, language model available).

1. **Title** — who it is for, input and output, four headline numbers.
2. **The problem** — the confidently-wrong risk; the five areas of a city.
3. **AI solution: a team of agents** — the eight agents, the three gates, and what
   each hands on.
4. **Worked example: Hyderabad, India** — source-to-finding funnel, findings per
   area, one finding, one withheld claim, open questions.
5. **Tech stack** — layer, choice and reason.
6. **Knowledge management and retrieval** — three stores, the question path.
7. **Trustworthiness and evidence** — five guards, with a passed and a withheld
   Hyderabad claim.
8. **Evaluation** — run metrics, where the time went, and an honest reading (grounded
   is not representative: the burden figure is a 40-patient hospital sample).
9. **Engineering decisions** — decisions with their measured effect.
10. **User experience** — five-step journey and depth options.
11. **Trade-offs and limitations** — impact and next step; demo path.

Quick (42 s) and Balanced (51 s, Hyderabad, one round) times are measured; Thorough
is an estimate and is labelled as such on slide 10.

## Demo path

1. Enter a city the audience picks.
2. Walk the workflow steps while the agents run.
3. Open a finding to its verbatim quote and source.
4. Read the gaps — what the system refused to claim, and why.
5. Open the **Knowledge graph** tab.
6. Ask a question; show the citations.
7. Download the brief.
8. Open **Admin / runtime** for quality metrics.
