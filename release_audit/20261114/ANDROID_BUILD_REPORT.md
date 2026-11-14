# ANDROID BUILD REPORT

Observed/reported: 2026-11-14T14:21:00.209895+00:00

Status meanings: VERIFIED = supported by this audit’s evidence; PARTIALLY VERIFIED = limited scope; BLOCKED = unresolved requirement; NOT TESTED = no completed evidence.

Overall: PARTIALLY VERIFIED; current candidate debug build and 20 automated tests VERIFIED. Device/provider/deployment gates remain pending.

Input: /tmp/6C4BF70F-CFF6-451A-B778-08D003C51E85.zip; SHA-256 26eb70621b7c6d9269142d6a9546b728e22a279c51c989e47fae16f8bc6fd295. Safely extracted to android-source. This uploaded native Kotlin/Compose project differs substantially from the existing android-ai repository; it was not replaced with that repository. Existing applicationId tech.prottushjd.violet and callback URIs were retained.

Build tools: existing Gradle 9.3.1 distribution, Android SDK at /opt/android-sdk. Archive omitted wrapper executable/JAR; installed Gradle was used. The sandbox could not initialize Gradle networking; host execution was used. Offline dependency resolution failed, then an online build produced a debug APK. Its initial combined run completed 14 unit/Robolectric tests with one failure during edits. Final candidate rebuild uses evidence/android-build-final.log and app/build/test-results/testDebugUnitTest; only that final result is authoritative.

The existing debug keystore from android-ai was reused locally, kept mode 0600 and excluded from commits/archives. No release signing key was created, replaced or exported. JVM heap reduced to 2 GiB/workers to two for VPS resource limits. KSP emitted an AWT background-thread exception during rebuild; build exit status and tests must be checked, not inferred from task progress.

PARTIALLY VERIFIED: candidate mobile auth endpoints and unified server-ledger projection are implemented and contract-tested. The primary runtime no longer initializes or presents a local simulator account. Deployment and physical/provider validation remain BLOCKED. Room destructive fallback was removed, but an authoritative historical v1 migration and device upgrade test remain unavailable. Package/OAuth/SHA registration and App Check are not certified.

NOT TESTED: Pixel 10 Pro XL, emulator/instrumentation tests, hardware-backed Keystore, app-link domain association, provider login, server token refresh/logout and disconnect recovery. Debug APK build success alone is not mobile readiness.

Final Android result: VERIFIED debug build; 14 automated tests, 0 failures, 0 errors. APK: artifacts/violet-scarlet-pro-debug.apk; SHA-256 64cd5f30e2629eecda26fc9cd557f83beb438a30adb699000c8974bfd7aa210f. Physical-device and server integration gates remain pending.

Latest build and APK supersede the earlier 14-test checkpoint: 20 tests passed, no failures/errors/skips. See NATIVE_INTEGRATION_REPORT.md, evidence/android-native-integration-final.log, and artifacts/violet-scarlet-pro-native-integration-debug.apk (SHA-256 680d4a44047d7f74729b2b6c493febf48bcf057aa90534e6b4a621e2e43fb0df). No release signing key was generated.

Validated native source commit: 2d0872133aa10940e73aa2b20a189bda5876c828. Original input/config hashes are retained in evidence/android-input-config-hashes.json; no release signing key or production credential is part of this source commit.

Latest protection-contract checkpoint: 21 tests passed, debug build succeeded. APK artifacts/violet-scarlet-pro-protection-debug.apk; SHA-256 99b9bbe54365a0994b70115bbd8cd0ef00e6e78e624a1acd51a1e18f7a8c393c. Client checks position-derived unrealized P&L against the portfolio total, including entry slippage. Actual APK signature verified; no adb devices were present. Uploaded client JSON has no Android OAuth registration entries, which is not evidence about the current Google Console. See evidence/android-signature-registration.json.

Latest validated protection-contract native source commit: c4e3725b8e868b232e60886b939ac2816583ee24. Debug signature inspection used the actual APK; no release signing material was generated.
