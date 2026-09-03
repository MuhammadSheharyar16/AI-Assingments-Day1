"""Day 6 — structured logs (Task 7), metrics (Task 8) and OpenTelemetry
tracing (Task 9) for the API service boundary.

Nothing in `aico.rag`/`aico.platform` (Day 3/5) imports from this
package - observability is layered on top of the request lifecycle in
`aico.api`, never mixed into the pipeline internals, per the working rule
"keep Day 5 as the internal RAG application flow"."""
