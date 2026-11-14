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

Validated backend candidate source commit: 69b421e12fad2b2c69c00668f9b79bb47d2da7b8. Android native source commit: 1c543250fc758aa76bb22b07a7509c7a40b507aa. Neither was deployed/pushed. Android build also used the uploaded .env.example and app/google-services.json; their checksums are recorded separately in evidence/android-input-config-hashes.json (values not disclosed). Backend source tests: 217 passed; Android 14 passed.

A second native-integration candidate has been validated locally (see NATIVE_INTEGRATION_REPORT.md). Latest APK: artifacts/violet-scarlet-pro-native-integration-debug.apk; SHA-256 680d4a44047d7f74729b2b6c493febf48bcf057aa90534e6b4a621e2e43fb0df. Native session storage is a separate sidecar, created lazily only when authentication is used. No production restart or deployment was performed. Candidate source commits are recorded after committing the reviewed source.

Validated native source commit: 2d0872133aa10940e73aa2b20a189bda5876c828. Original input/config hashes are retained in evidence/android-input-config-hashes.json; no release signing key or production credential is part of this source commit.

Native integration backend source commit: 7595055e2d3e59dd2c457a2e100c87fdfdd6a231. Native Android source commit: 2d0872133aa10940e73aa2b20a189bda5876c828. Both are local candidate commits, not deployed commits. Production base remains 0f2a0d2dd752eafdd59bbeb8d93f0c440bf9245a with local changes; exact loaded deployment provenance remains unverified. All paper services and Caddy retained their recorded PIDs and NRestarts=0 at the final checkpoint.

Protection candidate validated locally: 250 backend tests and 21 Android tests. Latest APK artifacts/violet-scarlet-pro-protection-debug.apk, SHA-256 99b9bbe54365a0994b70115bbd8cd0ef00e6e78e624a1acd51a1e18f7a8c393c. No production deployment or restart; candidate source commits recorded after review. See PAPER_PROTECTION_IMPLEMENTATION_REPORT.md and rollback limitations.

Validated protection backend source commit: 1d29f16a7ee44e5cf5e7d7cc011f844b4a51c3ad. Native contract source commit: c4e3725b8e868b232e60886b939ac2816583ee24. Both are local candidates; neither has been pushed or deployed. Existing production base remains 0f2a0d2dd752eafdd59bbeb8d93f0c440bf9245a with local modifications, so no exact deployed-commit certification is claimed.

Final funding-capture validation: backend 267 passed, 0 failed, 2 warnings in 63.43s; focused funding/mandatory gates 37 passed in 4.33s; frontend typecheck, 42 tests and build passed. Tested backend candidate source commit: e52906332c15faa098e449f911edc512a95727ec. This is a local candidate, not deployed or pushed. Commands/durations: evidence/validation-funding-commands.json; full test results: evidence/backend-funding-final.xml.

Funding source manifest: evidence/funding-source-manifest.json. Eight source files were committed with narrow staging; unrelated arbitrage/verification work remains untouched. Operating service inventory and exact deployed-commit uncertainty are unchanged.
