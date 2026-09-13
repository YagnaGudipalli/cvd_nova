---
title: CARDIO4Cities Intelligence Studio
emoji: 🫀
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Evidence-first cardiovascular health research for any city
---

# CARDIO4Cities Intelligence Studio

Live, evidence-first research on a city's cardiovascular health landscape, built for
City Leads preparing to meet government and healthcare stakeholders.

Enter a city, choose a research depth, and the system researches the public internet,
checks what it is permitted to crawl, extracts claims anchored to quotes located in
their sources, has a separate model verify each one, and writes what survives to a
relational ledger, a Qdrant vector store and a Graphiti knowledge graph in Neo4j.
What it cannot establish is published as a knowledge gap rather than filled in.

- **Workspace:** this Space's root URL
- **Architecture overview:** `/architecture.html`
- **Demonstration deck:** `/presentation.html`
- **API documentation:** `/docs`

The Space sleeps after a period without visitors; the first request after that takes
a minute or two to wake it. Research history is kept on the Space's local disk and
resets when it restarts; the vector store and knowledge graph are hosted externally
and persist.
