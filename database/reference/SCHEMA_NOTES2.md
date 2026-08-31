# BirdBrain schema v0.2

This revision removes the rolling handicap window and stores season-wide SHAM adjustments, payout configuration, official tie resolutions, postseason aggregation, and generated reports/recaps.

The SHAM slope/rating system activates only after both 11 completed rounds and 40 unique players. At five player rounds, the engine trims floor(n * 0.20) observations from each end and averages the middle values.

Standings and payout multipliers remain independent. DNFs can remain paid-in while receiving zero points. Captains may edit scores until finalization.
