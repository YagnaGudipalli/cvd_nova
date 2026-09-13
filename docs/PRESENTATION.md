# Demonstration deck

The deck is an interactive 8-slide page served at
[`/presentation.html`](../frontend/presentation.html)
(live: <https://cardio4cities-intelligence-studio.onrender.com/presentation.html>).

- Arrow keys, PageUp/PageDown, Home/End to navigate.
- **Save as PDF** button prints one 16:9 slide per page.

## Slides

Each slide is headed by its section title and kept to short pointers, one table or
one chart. Numbers come from the recorded Hyderabad run in
[`examples/hyderabad-research-run.json`](examples/hyderabad-research-run.json)
(Balanced depth, one round, 51 s, language model available).

| # | Slide | Case-study topic it covers |
|---|---|---|
| 1 | **Title**: who it is for, input and output, four headline numbers | |
| 2 | **The problem**: the confidently-wrong risk; the five areas of a city | The problem as understood |
| 3 | **AI solution: a team of agents**: eight agents, three gates, Tavily plus scholarly discovery, the stack | Solution and architecture |
| 4 | **Knowledge management and retrieval**: three stores, the question path | Solution and architecture |
| 5 | **Trustworthiness and evidence**: five guards, with a passed and a withheld Hyderabad claim | Trust and evidence |
| 6 | **Worked example and evaluation: Hyderabad**: run metrics, source-to-finding funnel, an honest reading | Trust and evidence |
| 7 | **User experience**: five-step journey; depth options with web-search budget | User experience |
| 8 | **Trade-offs and limitations**: decisions with their effect, limitations with next steps, demo path | Trade-offs and limitations |

Quick (42 s) and Balanced (51 s, Hyderabad, one round) times are measured; Thorough
is an estimate and is labelled as such on slide 7.

The Hyderabad run was recorded before Tavily web search was configured, so all 46 of
its sources are scholarly. Slide 6 says this, because it explains why the policy and
access areas came back as gaps.

## Demo path

1. Enter a city the audience picks.
2. Walk the workflow steps while the agents run. In **Find sources**, point out
   Tavily results (government and programme pages) next to scholarly ones.
3. Open a finding to its verbatim quote and source.
4. Read the gaps: what the system refused to claim, and why.
5. Open the **Knowledge graph** tab.
6. Ask a question; show the citations.
7. Download the brief.
8. Open **Admin / runtime** for quality metrics and provider status.
