# Employee5 V60 model notes

Core-line scoring rule:
- Full scan of completed aggregate bars.
- Body-top, close, high and upper-shadow resonance are positive evidence; more resonance is better.
- Body cut penalty should be progressive: 1 cut = 0, 2 cuts = -1, 3 cuts = -2, 4 cuts = -3, etc. Formula: `body_cut_penalty = max(0, body_cut_count - 1)`.
- Full entity acceptance remains a hard invalidation for current resistance.
- Escalate timeframe only when the current timeframe has a meaningful candidate but cannot form a core line after scoring, or is invalidated by entity acceptance.
