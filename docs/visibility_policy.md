# Actor Visibility Policy

`ActorView` is the trust boundary between raw simulation state and agent behavior.

Visibility scopes:

- `public`: visible to all actors
- `party`: visible to party actors
- `actor`: visible only to explicitly listed actors
- `hidden` / `private`: hidden unless reveal conditions are satisfied

Reveal conditions are deterministic and support flag checks, actor membership, boolean composition, and turn thresholds. Runtime and generation paths consume filtered `ActorView` data only.
