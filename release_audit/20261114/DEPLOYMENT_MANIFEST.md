# DEPLOYMENT MANIFEST

Observed/reported: 2026-11-14T14:21:00.209895+00:00

Status meanings: VERIFIED = supported by this audit’s evidence; PARTIALLY VERIFIED = limited scope; BLOCKED = unresolved requirement; NOT TESTED = no completed evidence.

Overall: PARTIALLY VERIFIED; no new production release deployed.

| Source | Recorded base commit | Branch | Discovery state |
|---|---|---|---|
| /root/multi-exchange-paper | 0f2a0d2dd752eafdd59bbeb8d93f0c440bf9245a | feature/violet-adaptive-paper-20261114 | dirty |
| /root/multi-exchange-paper-audit-fixes | 46c7a3612a50233b1bda428c45b5efa40959777f | upgrade/realtime-dashboard-charts | clean |
| /root/multi-exchange-paper-maintenance-20261114 | cc2b5c23c549daf73bb12e3fe9993f63716d4add | maintenance/observability-dashboard-20261114 | dirty |
| /root/violet-scarlet-pro-android-ai | 12da3a4d5c356ab5bff5fde06917e1039b6dc2e6 | master | clean |

Exact deployed commit cannot honestly be certified: production has tracked local modifications, preview_app.py is outside Git, and loaded process code is not proven equal to on-disk HEAD. The table is a base-commit inventory, not release certification. Candidate commit/checksum information is appended after final validation.

Running target services: multi-exchange-paper.service (PID 673, /root/multi-exchange-paper, original production domain through 127.0.0.1:8788); multi-exchange-paper-audit.service (PID 670, audit-fixes checkout, 127.0.0.2:8789); multi-exchange-paper-maintenance-preview.service (PID 5133, maintenance checkout with external preview_app.py, 127.0.0.3:8790). Caddy PID 662 routes trade.* to the original engine and preview.trade.*, prottushjd.tech and prottushjdebnath.tech to preview. Runtime inventory is timestamped and PIDs may change. All were active with NRestarts=0 at inspection; this is not full soak evidence.

Active campaign pointer: /root/paper-maintenance-preview-data/active-violet-campaign.json → violet-all-cap-500-fresh-v2. No campaign/position reset. Unrelated Solana, SSH, Docker and other host services preserved. No new remote push: a destination/branch authorization has not been established for this release.

Checkpoint hashes: evidence/discovery.json. Evidence checksums: SHA256SUMS. Debug APK details are added only after final rebuild. No release signing material is included.

Final Android result: VERIFIED debug build; 14 automated tests, 0 failures, 0 errors. APK: artifacts/violet-scarlet-pro-debug.apk; SHA-256 64cd5f30e2629eecda26fc9cd557f83beb438a30adb699000c8974bfd7aa210f. Physical-device and server integration gates remain pending.
