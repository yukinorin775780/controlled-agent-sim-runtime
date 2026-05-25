# Architecture

Controlled Agent Sim Runtime uses a transport-neutral `GameService` boundary. The web UI, CLI, eval runner, benchmark script, and API all use the same service path.

The main contract is simple:

- agents receive filtered `ActorView` objects, not raw global state
- LLM nodes can interpret or express behavior, but deterministic systems own state mutation
- every mutation is represented as a typed `DomainEvent`
- `EventDrain` applies events into the authoritative `GameState`
- golden replay cases validate behavior without external model calls

The goal is bounded autonomy: agents can feel stateful and responsive without getting unchecked write access to the simulation.
