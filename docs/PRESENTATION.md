# CARDIO4Cities Demo Deck

## Slide 1: The meeting starts before the meeting

City Leads need a reliable view of health burden, programs, policy, stakeholders, opportunities, risks, and unknowns before engaging decision-makers.

## Slide 2: The product

Enter an unseen city and receive a live, evidence-first intelligence brief with workflow visibility, source quotes, graph relationships, Q&A, gaps, and a downloadable report.

## Slide 3: Agentic workflow

Intake guardrails -> research planning -> live discovery -> crawlability detection -> extraction -> independent fact checking -> datastore indexing -> report and Q&A.

## Slide 4: Trust architecture

Evidence is mandatory. Scope is explicit. Unsupported claims are withheld. Every answer links to its source. Missing information is shown as a knowledge gap.

## Slide 5: Data architecture

SQLite/managed relational database for audit records, Qdrant for semantic evidence retrieval, and Graphiti plus Neo4j Sandbox for entities and relationships.

## Slide 6: User experience

Search is the first action. Plain-language progress updates explain what the system is doing. Evidence is expandable. The graph, Q&A, history, report, and admin telemetry are separate focused views.

## Slide 7: Evaluation and observability

The admin view tracks source counts, extraction rate, evidence coverage, city-scope quality, gaps, provider errors, guardrails, stage durations, and trace events.

## Slide 8: Trade-offs and next steps

The timeboxed MVP prioritizes trustworthy end-to-end behavior. Next steps are managed PostgreSQL, stronger source ranking, richer Graphiti schemas, authentication, and a deployed production domain.