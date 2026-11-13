from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import ExchangeCredentials
from app.core.models import (
    Direction,
    ExchangeId,
    MarketType,
    OrderStatus,
    OrderType,
    PaperFill,
    PaperOrder,
    PaperPosition,
    Side,
)
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.miniapp_presenter import build_miniapp_state_payload
from app.dashboard.server import create_dashboard_app
from app.exchanges.registry import ExchangeRegistry
from tests.test_v7_miniapp_contract import _FakeAdapter, _make_v7_service, _sign_init_data


def _seed_closure_order(
    svc,
    *,
    order_id: str,
    bot_id: str,
    exchange: ExchangeId,
    symbol: str,
    side: Side,
    qty: float,
    entry_price: float,
    exit_price: float,
    leverage: float,
    fee_usd: float,
    gross_realized: float,
    ts: datetime,
) -> None:
    ord_obj = PaperOrder(
        order_id=order_id,
        client_order_id=f"cid_{order_id}",
        bot_id=bot_id,
        signal_id=f"sig_{order_id}",
        exchange=exchange,
        symbol=symbol,
        market_type=MarketType.PERP,
        side=side,
        order_type=OrderType.MARKET,
        requested_qty=qty,
        filled_qty=qty,
        avg_fill_price=exit_price,
        leverage=leverage,
        fee_usd=fee_usd,
        status=OrderStatus.FILLED,
        is_paper=True,
        created_at=ts,
        updated_at=ts,
    )
    fill_obj = PaperFill(
        order_id=order_id,
        bot_id=bot_id,
        exchange=exchange,
        symbol=symbol,
        market_type=MarketType.PERP,
        side=side,
        qty=qty,
        price=exit_price,
        fee_usd=fee_usd,
        slippage_bps=1.0,
        is_paper=True,
        timestamp=ts,
    )
    with svc.db.transaction() as conn:
        svc.db.save_order(ord_obj, conn=conn)
        svc.db.save_fill(fill_obj, conn=conn)
        conn.execute(
            """
            INSERT INTO paper_realized_closures
            (order_id, bot_id, exchange, symbol, market_type, close_qty, entry_price, exit_price, gross_realized_usd, exit_fee_usd, is_paper, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                order_id,
                bot_id,
                exchange.value,
                symbol,
                MarketType.PERP.value,
                str(qty),
                str(entry_price),
                str(exit_price),
                str(gross_realized),
                str(fee_usd),
                ts.isoformat(),
            ),
        )


def test_per_exchange_equity_and_margin_notional_separation(tmp_path: Path) -> None:
    svc, _ = _make_v7_service(tmp_path)
    bots = svc.bot_manager.list_bots_status()
    okx_bot = next(b["bot_id"] for b in bots if b["exchange"] == "okx")
    base_accounts = svc.db.list_accounts()
    base_total_cash = sum(float(a["cash_usd"]) for a in base_accounts)
    okx_initial_cash = sum(float(a["cash_usd"]) for a in base_accounts if a["exchange"] == "okx")

    # Open leveraged OKX position: notional = 0.1 * 9000 = 900, lev = 3x => reserved margin = 300
    entry_ord = PaperOrder(
        order_id="entry_1",
        client_order_id="cid_entry_1",
        bot_id=okx_bot,
        signal_id="sig_entry_1",
        exchange=ExchangeId.OKX,
        symbol="ETH/USDT",
        market_type=MarketType.PERP,
        side=Side.BUY,
        order_type=OrderType.MARKET,
        requested_qty=0.1,
        filled_qty=0.1,
        avg_fill_price=9000.0,
        leverage=3.0,
        fee_usd=0.5,
        status=OrderStatus.FILLED,
        is_paper=True,
    )
    with svc.db.transaction() as conn:
        svc.db.save_order(entry_ord, conn=conn)
        svc.db.update_account_cash(
            okx_bot,
            cash_delta=-0.5,
            realized_pnl_delta=-0.5,
            reserved_margin_delta=300.0,
            conn=conn,
        )
        svc.db.upsert_position(
            PaperPosition(
                bot_id=okx_bot,
                exchange=ExchangeId.OKX,
                symbol="ETH/USDT",
                market_type=MarketType.PERP,
                direction=Direction.LONG,
                qty=0.1,
                entry_price=9000.0,
                mark_price=8800.0,
                leverage=3.0,
                unrealized_pnl_usd=-20.0,
            ),
            conn=conn,
        )
        svc.db.save_fill(
            PaperFill(
                order_id="entry_1",
                bot_id=okx_bot,
                exchange=ExchangeId.OKX,
                symbol="ETH/USDT",
                market_type=MarketType.PERP,
                side=Side.BUY,
                qty=0.1,
                price=9000.0,
                fee_usd=0.5,
                slippage_bps=1.0,
                is_paper=True,
                timestamp=datetime.now(timezone.utc),
            ),
            conn=conn,
        )

    payload = build_miniapp_state_payload(svc, "tg:42")
    risk = payload["risk"]
    okx = payload["exchanges"]["okx"]

    expected_eq = round(base_total_cash - 0.5 - 20.0, 2)
    assert risk["total_paper_equity_usd"] == expected_eq
    sum_ex_eq = round(
        sum(payload["exchanges"][ex]["paper_equity_usd"] for ex in ("binance", "bybit", "okx")),
        2,
    )
    assert sum_ex_eq == risk["total_paper_equity_usd"]

    # Margin vs Notional must NOT be conflated
    assert okx["locked_margin_usd"] == 300.0
    assert okx["available_margin_usd"] == round(okx_initial_cash - 0.5 - 300.0, 2)
    assert okx["notional_exposure_usd"] == 880.0
    assert risk["reserved_margin_usd"] == 300.0
    assert risk["gross_margin_exposure_usd"] == 300.0
    assert risk["gross_notional_exposure_usd"] == 880.0
    assert risk["free_paper_capital_usd"] == round(base_total_cash - 0.5 - 300.0, 2)
    assert risk["margin_allocation_pct"] == round((300.0 / expected_eq) * 100.0, 2)
    assert risk["notional_exposure_pct"] == round((880.0 / expected_eq) * 100.0, 2)


def test_realized_pnl_utc_cutoff_and_unbacked_legacy_handling(tmp_path: Path) -> None:
    svc, _ = _make_v7_service(tmp_path)
    bots = svc.bot_manager.list_bots_status()
    binance_bot = next(b["bot_id"] for b in bots if b["exchange"] == "binance")

    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    today_early = now.replace(hour=0, minute=5, second=0, microsecond=0)
    if today_early > now:
        today_early = now - timedelta(seconds=30)

    # Yesterday trade: -25.0 gross, -1.0 fee => net -26.0
    _seed_closure_order(
        svc,
        order_id="ord_yest",
        bot_id=binance_bot,
        exchange=ExchangeId.BINANCE,
        symbol="BTC/USDT",
        side=Side.SELL,
        qty=0.01,
        entry_price=90000.0,
        exit_price=87500.0,
        leverage=2.0,
        fee_usd=1.0,
        gross_realized=-25.0,
        ts=yesterday,
    )
    # Today UTC trade: +10.0 gross, -1.0 fee => net +9.0
    _seed_closure_order(
        svc,
        order_id="ord_today",
        bot_id=binance_bot,
        exchange=ExchangeId.BINANCE,
        symbol="BTC/USDT",
        side=Side.SELL,
        qty=0.01,
        entry_price=88000.0,
        exit_price=89000.0,
        leverage=2.0,
        fee_usd=1.0,
        gross_realized=10.0,
        ts=today_early,
    )
    svc.db.update_account_cash(binance_bot, cash_delta=-17.0, realized_pnl_delta=-17.0)

    payload = build_miniapp_state_payload(svc, "tg:42")
    risk = payload["risk"]
    assert risk["daily_realized_available"] is True
    assert risk["daily_realized_pnl_usd"] == 9.0
    assert risk["lifetime_realized_pnl_usd"] == -17.0
    assert payload["analytics"]["closed_trades"] == 2
    assert payload["analytics"]["win_rate_pct"] == 50.0
    assert payload["analytics"]["profit_factor"] == round(9.0 / 26.0, 2)
    # Equity curve strictly ascending
    times = [pt["time"] for pt in payload["analytics"]["equity_curve"]]
    assert times == sorted(set(times))
    # Closed perp position surfaced for History tab
    closed_positions = [p for p in payload["futures_positions"] if p["status"] == "closed"]
    assert len(closed_positions) == 2

    # Inject unbacked legacy delta without closure/fill rows -> daily becomes Unavailable (None)
    svc.db.update_account_cash(binance_bot, cash_delta=-50.0, realized_pnl_delta=-50.0)
    unbacked = build_miniapp_state_payload(svc, "tg:42")
    assert unbacked["risk"]["daily_realized_available"] is False
    assert unbacked["risk"]["daily_realized_pnl_usd"] is None
    assert unbacked["risk"]["lifetime_realized_pnl_usd"] == -67.0
    assert unbacked["exchanges"]["binance"]["realized_pnl_24h"] is None
    assert unbacked["exchanges"]["binance"]["realized_pnl_24h_available"] is False
    assert unbacked["exchanges"]["binance"]["lifetime_realized_pnl"] == -67.0


def test_hwm_drawdown_unavailable_vs_tracked_and_auth_states(tmp_path: Path) -> None:
    svc, tg_cfg = _make_v7_service(tmp_path)
    bots = svc.bot_manager.list_bots_status()
    okx_bot = next(b["bot_id"] for b in bots if b["exchange"] == "okx")
    base_total = sum(float(a["cash_usd"]) for a in svc.db.list_accounts())
    loss = round(base_total * 0.012, 2)
    svc.db.update_account_cash(okx_bot, cash_delta=-loss, realized_pnl_delta=-loss)
    current_eq = round(base_total - loss, 2)

    # Before HWM tracking is initialized, drawdown must be None / Unavailable (never fake 0.00%)
    before = build_miniapp_state_payload(svc, "tg:42")
    assert before["risk"]["drawdown_available"] is False
    assert before["risk"]["current_drawdown_pct"] is None
    assert before["risk"]["hwm_equity_usd"] is None

    # After update_equity_watermarks with initial_equity_usd=base_total -> DD=1.20%
    svc.risk.update_equity_watermarks(current_eq, initial_equity_usd=base_total)
    after = build_miniapp_state_payload(svc, "tg:42")
    assert after["risk"]["drawdown_available"] is True
    assert after["risk"]["hwm_equity_usd"] == round(base_total, 2)
    assert after["risk"]["current_drawdown_pct"] == 1.2
    assert after["risk"]["live_readonly_auth_summary"]["authenticated_exchanges"] == ["okx"]
    assert after["risk"]["total_live_readonly_equity_usd"] is None

    # Verify HTTP auth & CSP security intact and no API secrets leaked
    okx = _FakeAdapter(
        ExchangeId.OKX,
        ExchangeCredentials(exchange="okx", api_key="SUPER_SECRET_KEY", api_secret="SUPER_SECRET_PASS", passphrase="p"),
    )
    bybit = _FakeAdapter(ExchangeId.BYBIT, ExchangeCredentials(exchange="bybit"))
    binance = _FakeAdapter(ExchangeId.BINANCE, ExchangeCredentials(exchange="binance"))
    svc.registry = ExchangeRegistry({ExchangeId.OKX: okx, ExchangeId.BYBIT: bybit, ExchangeId.BINANCE: binance})
    app = create_dashboard_app(svc, auth_config=DashboardAuthConfig("admin", "secret"))
    client = TestClient(app)

    unauthed = client.get("/api/miniapp/state")
    assert unauthed.status_code == 401

    valid_init = _sign_init_data(tg_cfg.bot_token, int(tg_cfg.chat_id))
    authed = client.get("/api/miniapp/state", headers={"X-Telegram-Init-Data": valid_init})
    assert authed.status_code == 200
    assert "SUPER_SECRET_KEY" not in authed.text
    assert "SUPER_SECRET_PASS" not in authed.text
    assert authed.headers.get("X-Frame-Options") == "DENY"
    assert authed.headers.get("X-Content-Type-Options") == "nosniff"
