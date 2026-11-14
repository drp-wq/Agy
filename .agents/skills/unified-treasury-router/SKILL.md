---
name: unified-treasury-router
description: Shared AGY + Codex skill for proposing and simulating closed-loop capital allocations and auto-sweeps across the Unified Hub, 3 CEXs (Binance, OKX, Bybit), and Solana DEX.
---

# Unified Treasury Router Skill

Use this skill when proposing, simulating, or auditing capital movement in the closed-loop trading architecture.

## Closed-Loop Venues (v1)
- `hub_binance_master` (Primary Custody & Settlement Hub)
- `cex_binance` (Binance Spot/Perp)
- `cex_okx` (OKX Spot/Perp)
- `cex_bybit` (Bybit Spot/Perp)
- `dex_solana` (Solana DEX Wallet — Jupiter/Raydium)

## Operations
1. **Allocate (`request_transfer`)**: Move capital from `hub_binance_master` to a CEX or DEX venue when strategy risk-adjusted score justifies capital.
2. **Sweep (`auto_sweep_idle_capital`)**: Return idle capital or realized profits above `target_usd + hysteresis_usd` back to `hub_binance_master`.
3. **Invariant Check (`check_equity_invariant`)**: Verify `Total Equity == Hub + Sum(CEX) + Sum(DEX) + InFlight - CumulativeFees`.
4. **Emergency Halt**: Respect `GlobalRiskGuardian.emergency_stop_active` and `circuit_breaker_tripped` — never bypass.

## Safety Constraints
- Only `PaperSimulationTransport` is active in Phases 1–3 (`live_execution_enabled: false`).
- Reject any transfer whose destination is not in `ClosedLoopAllowlist`.
- Reject any transfer where `estimated_fee_usd / amount_usd > max_transfer_fee_pct` (default `0.25%`).
- Reject any transfer that pushes a non-Hub venue above `max_venue_concentration_pct` (default `35%`).
