# STABILITY TEST RESULTS

Observed/reported: 2026-11-14T14:21:00.209895+00:00

Status meanings: VERIFIED = supported by this audit’s evidence; PARTIALLY VERIFIED = limited scope; BLOCKED = unresolved requirement; NOT TESTED = no completed evidence.

Overall: PARTIALLY VERIFIED; the required 72-hour gate has NOT passed.

Campaign: violet-all-cap-500-fresh-v2. Durable start 2026-11-14T09:53:05.553807+00:00; planned end 2026-11-17T09:53:05.553807+00:00. Observation window has not been shortened.

As of 2026-11-14T14:18:27.036817+00:00: 156 samples from 2026-11-14T09:53:35.669715+00:00 to 2026-11-14T14:18:24.865631+00:00; observed span 4.4137 hours. Maximum inter-sample gap 931.293478 seconds; 1 degraded/unhealthy samples; 0 out-of-order timestamps. A degraded sample occurred at 11:40:07 UTC; gaps exceeding five minutes occurred 11:25:34–11:40:07 and 11:50:05–12:05:36 UTC. The existing record therefore does not establish uninterrupted healthy operation.

VERIFIED: existing monitoring timers are present; operating services were active at inspection; fixture recovery/idempotency tests pass. Full evidence: evidence/stability-current.json, timers.txt, services.txt. Operating observations are retained in the campaign’s 72h-observations.jsonl.

NOT TESTED: production worker crash injection, database outage injection, intentional container restart, OAuth refresh recovery, and sustained mobile reconnection. Existing services were not restarted merely to claim a recovery pass. No running Docker containers were listed. A future end timestamp is a schedule, not a passing result. Analyze recorded failures before classifying the eventual window.

Latest sample check (2026-11-14T14:41:45.019205+00:00): 172 samples spanning 4.7857 hours, one degraded sample, maximum gap 931.293478s; latest fill/position counts 1/1. Required 72-hour gate remains NOT PASSED. Historical journal exception classes include ValueError, NetworkError and ExchangeOutageError; error counts alone do not establish a root cause.

Protection checkpoint (2026-11-14T15:13:39.628396+00:00): 194 samples across 5.3200 hours; one degraded sample; maximum gap 931.293478s. No pass. Observations concern the existing running version, not the undeployed protection candidate.
