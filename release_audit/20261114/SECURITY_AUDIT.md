# SECURITY AUDIT

Observed/reported: 2026-11-14T14:21:00.209895+00:00

Status meanings: VERIFIED = supported by this audit’s evidence; PARTIALLY VERIFIED = limited scope; BLOCKED = unresolved requirement; NOT TESTED = no completed evidence.

Overall: PARTIALLY VERIFIED.

VERIFIED: anonymous production root and campaign API return 401; invalid bearer token returns 401; authenticated read-only campaign POST returns 405; authenticated preview dashboard returns 200. TLS verification was enabled for HTTPS probes. Backend suites exercise live-mode rejection, exchange write-method/transport guards, redaction, secret permissions, OAuth allowlisting/CSRF and Telegram authentication. Candidate async telemetry now applies the same read-only exchange guard. No live orders, transfers, withdrawals or wallet actions were attempted.

Android candidate fixes: remove silent fallback from Android Keystore to ephemeral in-memory production crypto; remove destructive Room fallback; preserve unsupported/legacy ledger state on initialization; remove fabricated 16,000 USDT archive entry on a fresh install; require affirmative paper-mode and execution-disabled fields in successful health responses; match exact callback paths. Cleartext traffic is disabled by the supplied manifest and TLS trust was not weakened.

BLOCKED: Android mobile code-exchange/login POST routes returned 405. Backend bearer session verification, refresh/revocation, PKCE handoff and correct provider registration are not demonstrated end-to-end. Existing web Google sign-in is a different session flow. Firebase/App Check enforcement is not established: dependencies/configuration alone do not prove runtime attestation or backend verification. Existing Room v1 migration cannot be certified without its authoritative schema. Administrative/WebSocket authorization and replay protection need broader runtime adversarial validation. Production is a dirty checkout, so passing candidate tests cannot certify its exact loaded code.

Exchange permission findings: OKX and Bybit keys have trading permissions, unchanged by this audit. Application locks remain mandatory. No independent OS/network isolation proof prevents every possible process from using externally trade-capable keys. Service processes run as root; isolation hardening remains incomplete.

NOT TESTED: authenticated Google provider interaction, Pixel hardware Keystore, physical-device TLS/network behavior, full API/queue/worker penetration testing. Secret values, signing material and credential changes are excluded from commits and reports.
