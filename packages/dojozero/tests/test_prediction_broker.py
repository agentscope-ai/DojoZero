"""Unit tests for :class:`PredictionBroker`."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast

import pytest

from dojozero.betting._models import EventStatus, Prediction, PredictionOutcome
from dojozero.betting._prediction_broker import (
    PredictionBroker,
    _parse_clock_to_seconds,
    _parse_soccer_clock_to_elapsed_seconds,
)
from dojozero.core._types import StreamEvent
from dojozero.data._models import (
    BaseGameUpdateEvent,
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
)


def _broker(
    *, window_pools: list[int] | None = None, sport_type: str = ""
) -> PredictionBroker:
    config = {"actor_id": "prediction_broker", "trial_id": "trial-1"}
    if window_pools is not None:
        config["window_pools"] = window_pools  # type: ignore[assignment]
    return PredictionBroker(config, "trial-1", sport_type=sport_type)  # type: ignore[arg-type]


def _envelope(payload):  # type: ignore[no-untyped-def]
    return StreamEvent(stream_id="test", payload=payload)


def _game_init() -> GameInitializeEvent:
    return GameInitializeEvent(
        game_id="game-1",
        sport="nba",
        home_team="Lakers",
        away_team="Celtics",
        game_time=datetime(2026, 5, 30, 19, 30, tzinfo=timezone.utc),
    )


def _game_update(
    period: int, game_clock: str = "06:00", sport: str = "nba"
) -> BaseGameUpdateEvent:
    return BaseGameUpdateEvent(
        game_id="game-1",
        sport=sport,
        period=period,
        game_clock=game_clock,
        game_time_utc=datetime(2026, 5, 30, 20, 0, tzinfo=timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Configuration / construction
# ---------------------------------------------------------------------------


def test_rejects_window_pools_with_wrong_length() -> None:
    with pytest.raises(ValueError, match="window_pools"):
        _broker(window_pools=[1, 2, 3])


def test_accepts_default_window_pools() -> None:
    broker = _broker()
    rules = broker.get_rules()
    assert rules["window_pools"] == [5000, 4000, 3000, 2000, 500]
    assert rules["kind"] == "window_pool_prediction"


def test_window_labels_are_sport_specific_for_soccer() -> None:
    rules = _broker(sport_type="world_cup").get_rules()
    labels = [w["label"] for w in rules["windows"]]
    assert labels == [
        "Pre-game",
        "1st Half",
        "2nd Half",
        "Extra Time 1",
        "Extra Time 2",
    ]
    # The human-readable description must not advertise basketball quarters.
    assert "Q1" not in rules["description"]


def test_window_labels_are_quarters_for_nba() -> None:
    rules = _broker(sport_type="nba").get_rules()
    labels = [w["label"] for w in rules["windows"]]
    assert labels == ["Pre-game", "Q1", "Q2", "Q3", "Q4"]


def test_window_labels_fall_back_to_generic_periods() -> None:
    rules = _broker(sport_type="").get_rules()
    labels = [w["label"] for w in rules["windows"]]
    assert labels == ["Pre-game", "Period 1", "Period 2", "Period 3", "Period 4"]


def test_get_rules_advertises_pre_fold_pools() -> None:
    """get_rules() reports the configured (pre-fold) pools and flags that they
    can fold at settlement. The settled payout can exceed the advertised pool —
    e.g. window 2 advertises 3000 here but settles at 5500 for a regulation
    soccer match (see test_settlement_folds_unreached_window_pools_into_last_reached).
    """
    rules = _broker().get_rules()
    assert rules["window_pools"] == [5000, 4000, 3000, 2000, 500]
    assert rules["windows"][2]["pool"] == 3000  # configured / pre-fold
    assert rules["pools_are_pre_fold"] is True


@pytest.mark.parametrize("clock", ["AET", "PEN"])
def test_soccer_terminal_clock_without_period_maps_to_regulation_complete(
    clock: str,
) -> None:
    assert _parse_soccer_clock_to_elapsed_seconds(clock, 0, 45 * 60) == 90 * 60


# ---------------------------------------------------------------------------
# Event bootstrap & lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_game_initialize_creates_event() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))

    assert broker.current_event is not None
    assert broker.current_event.event_id == "game-1"
    assert broker.current_event.status == EventStatus.SCHEDULED


@pytest.mark.asyncio
async def test_handle_game_initialize_ignores_different_event_id() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))

    other = GameInitializeEvent(
        game_id="other-game",
        sport="nba",
        home_team="Bulls",
        away_team="Heat",
    )
    await broker.handle_stream_event(_envelope(other))

    # Tracked event must not be overwritten by an unrelated game_id.
    assert broker.current_event is not None
    assert broker.current_event.event_id == "game-1"
    assert broker.current_event.home_team == "Lakers"


@pytest.mark.asyncio
async def test_game_start_then_result_settles() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="nba"))
    )
    assert broker.current_event is not None
    assert broker.current_event.status == EventStatus.LIVE

    result = GameResultEvent(
        game_id="game-1",
        sport="nba",
        winner="home",
        home_score=110,
        away_score=100,
    )
    await broker.handle_stream_event(_envelope(result))

    assert broker.current_event is not None
    assert broker.current_event.status == EventStatus.SETTLED


@pytest.mark.asyncio
async def test_pending_status_replayed_after_init() -> None:
    """GameStart that arrives before init is buffered and replayed."""
    broker = _broker()

    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="nba"))
    )
    # Without an event yet, the start gets buffered, broker stays event-less.
    assert broker.current_event is None

    await broker.handle_stream_event(_envelope(_game_init()))

    # Init replayed the buffered start → status should advance to LIVE.
    assert broker.current_event is not None
    assert broker.current_event.status == EventStatus.LIVE


# ---------------------------------------------------------------------------
# submit_prediction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_prediction_returns_prediction_on_success() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))

    result = await broker.submit_prediction("alice", "game-1", "home_win")

    assert isinstance(result, Prediction)
    assert result.selection == PredictionOutcome.HOME_WIN
    assert result.window == 0  # pre-game
    assert result.agent_id == "alice"


@pytest.mark.asyncio
async def test_submit_prediction_rejects_invalid_event_id() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))

    result = await broker.submit_prediction("alice", "nope", "home_win")

    assert isinstance(result, str)
    assert "Invalid event ID" in result


@pytest.mark.asyncio
async def test_submit_prediction_rejects_invalid_selection() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))

    result = await broker.submit_prediction("alice", "game-1", "winner_winner")

    assert isinstance(result, str)
    assert "Invalid selection" in result


@pytest.mark.asyncio
async def test_submit_prediction_replaces_previous_in_same_window() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))

    first = await broker.submit_prediction("alice", "game-1", "home_win")
    second = await broker.submit_prediction("alice", "game-1", "away_win")

    assert isinstance(first, Prediction)
    assert isinstance(second, Prediction)
    assert first.prediction_id != second.prediction_id

    # Only the latest survives.
    preds = await broker.get_my_predictions("alice", "game-1")
    assert [p.prediction_id for p in preds] == [second.prediction_id]
    assert preds[0].selection == PredictionOutcome.AWAY_WIN


@pytest.mark.asyncio
async def test_submit_prediction_rejected_when_closed() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="nba"))
    )
    await broker.handle_stream_event(
        _envelope(
            GameResultEvent(
                game_id="game-1",
                sport="nba",
                winner="home",
                home_score=1,
                away_score=0,
            )
        )
    )

    # Event is now SETTLED.
    result = await broker.submit_prediction("alice", "game-1", "home_win")
    assert isinstance(result, str)
    assert "settled" in result.lower()


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settlement_scores_correct_predictions() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))

    await broker.submit_prediction("alice", "game-1", "home_win")
    await broker.submit_prediction("bob", "game-1", "away_win")

    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="nba"))
    )
    await broker.handle_stream_event(
        _envelope(
            GameResultEvent(
                game_id="game-1",
                sport="nba",
                winner="home",
                home_score=99,
                away_score=98,
            )
        )
    )

    alice_pred = (await broker.get_my_predictions("alice", "game-1"))[0]
    bob_pred = (await broker.get_my_predictions("bob", "game-1"))[0]
    assert alice_pred.is_correct is True
    assert bob_pred.is_correct is False


@pytest.mark.asyncio
async def test_unrecognised_winner_falls_back_to_even() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.submit_prediction("alice", "game-1", "even")
    await broker.submit_prediction("bob", "game-1", "home_win")

    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="nba"))
    )
    # Bogus winner with no tied final_score → broker defaults to "even"
    # rather than stalling settlement.
    await broker.handle_stream_event(
        _envelope(
            GameResultEvent(
                game_id="game-1",
                sport="nba",
                winner="???",
                home_score=99,
                away_score=98,
            )
        )
    )

    assert broker.current_event is not None
    assert broker.current_event.status == EventStatus.SETTLED
    alice_pred = (await broker.get_my_predictions("alice", "game-1"))[0]
    bob_pred = (await broker.get_my_predictions("bob", "game-1"))[0]
    assert alice_pred.is_correct is True  # picked "even", winner forced to "even"
    assert bob_pred.is_correct is False


@pytest.mark.asyncio
async def test_settles_with_no_predictions_safely() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="nba"))
    )
    await broker.handle_stream_event(
        _envelope(
            GameResultEvent(
                game_id="game-1",
                sport="nba",
                winner="home",
                home_score=1,
                away_score=0,
            )
        )
    )

    assert broker.current_event is not None
    assert broker.current_event.status == EventStatus.SETTLED


@pytest.mark.asyncio
async def test_settlement_folds_unreached_window_pools_into_last_reached() -> None:
    """A regulation soccer match (no extra time) reaches only windows 0-2, so
    the unreached extra-time pools (windows 3-4) must fold into window 2 — the
    full configured prize stays winnable instead of being silently dropped."""
    broker = _broker()  # pools [5000, 4000, 3000, 2000, 500]
    await broker.handle_stream_event(_envelope(_game_init()))

    # One sole correct prediction in each reachable regulation window.
    await broker.submit_prediction("alice", "game-1", "home_win")  # window 0 (pre)
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="world_cup"))
    )
    await broker.handle_stream_event(
        _envelope(_game_update(period=1, sport="world_cup"))
    )
    await broker.submit_prediction("bob", "game-1", "home_win")  # window 1 (1st half)
    await broker.handle_stream_event(
        _envelope(_game_update(period=2, sport="world_cup", game_clock="FT"))
    )
    await broker.submit_prediction("carol", "game-1", "home_win")  # window 2 (2nd half)

    # Regulation finish: the latest period reached is 2.
    await broker.handle_stream_event(
        _envelope(
            GameResultEvent(
                game_id="game-1",
                sport="world_cup",
                winner="home",
                home_score=2,
                away_score=1,
            )
        )
    )

    alice = (await broker.get_my_predictions("alice", "game-1"))[0]
    bob = (await broker.get_my_predictions("bob", "game-1"))[0]
    carol = (await broker.get_my_predictions("carol", "game-1"))[0]
    assert (alice.window, bob.window, carol.window) == (0, 1, 2)
    assert alice.is_correct and bob.is_correct and carol.is_correct
    assert alice.score == Decimal("5000")
    assert bob.score == Decimal("4000")
    # window 2 absorbs the unreached extra-time pools: 3000 + 2000 + 500.
    assert carol.score == Decimal("5500")
    # Full configured prize (14500) is awarded — nothing wasted.
    total = sum((p.score or Decimal(0) for p in (alice, bob, carol)), Decimal(0))
    assert total == Decimal("14500")


@pytest.mark.asyncio
async def test_settlement_does_not_fold_when_final_window_reached() -> None:
    """A match that reaches the final window (e.g. soccer extra time) awards
    pools exactly as configured — no folding."""
    broker = _broker()  # pools [5000, 4000, 3000, 2000, 500]
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="world_cup"))
    )
    await broker.handle_stream_event(
        _envelope(_game_update(period=4, sport="world_cup", game_clock="AET"))
    )
    await broker.submit_prediction("alice", "game-1", "home_win")  # window 4
    await broker.handle_stream_event(
        _envelope(
            GameResultEvent(
                game_id="game-1",
                sport="world_cup",
                winner="home",
                home_score=2,
                away_score=1,
            )
        )
    )
    alice = (await broker.get_my_predictions("alice", "game-1"))[0]
    assert alice.window == 4
    assert alice.is_correct
    assert alice.score == Decimal("500")  # final-window pool, unfolded


@pytest.mark.asyncio
async def test_settlement_without_game_updates_leaves_pools_unmodified() -> None:
    """If a GameResultEvent arrives before any period update, _last_reached_window
    is None and pools are awarded unmodified (no fold)."""
    broker = _broker()  # pools [5000, 4000, 3000, 2000, 500]
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.submit_prediction("alice", "game-1", "home_win")  # window 0 (pre)
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="world_cup"))
    )
    # No BaseGameUpdateEvent -> _recent_game_updates stays empty.
    await broker.handle_stream_event(
        _envelope(
            GameResultEvent(
                game_id="game-1",
                sport="world_cup",
                winner="home",
                home_score=1,
                away_score=0,
            )
        )
    )
    alice = (await broker.get_my_predictions("alice", "game-1"))[0]
    assert alice.window == 0
    assert alice.is_correct
    # Unmodified window-0 pool — no extra-time pools folded in.
    assert alice.score == Decimal("5000")


@pytest.mark.asyncio
async def test_settled_event_info_reports_last_reached_window() -> None:
    """After a regulation soccer match settles, current_window is the 2nd-half
    window (2), not the final extra-time window (4)."""
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="world_cup"))
    )
    await broker.handle_stream_event(
        _envelope(_game_update(period=2, sport="world_cup", game_clock="FT"))
    )
    await broker.handle_stream_event(
        _envelope(
            GameResultEvent(
                game_id="game-1",
                sport="world_cup",
                winner="home",
                home_score=2,
                away_score=1,
            )
        )
    )
    info = await broker.get_event_info()
    assert info is not None
    assert info["status"] == EventStatus.SETTLED.value
    assert info["current_window"] == 2


# ---------------------------------------------------------------------------
# Window resolution / elapsed-ratio computation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_window_uses_period_during_live_play() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="nba"))
    )

    # Q2 update → submission lands in window 2.
    await broker.handle_stream_event(_envelope(_game_update(period=2)))

    result = await broker.submit_prediction("alice", "game-1", "home_win")
    assert isinstance(result, Prediction)
    assert result.window == 2


@pytest.mark.asyncio
async def test_resolve_window_caps_overtime_at_q4() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="nba"))
    )

    # Overtime (period=5) clamps to NUM_WINDOWS-1 = 4.
    await broker.handle_stream_event(_envelope(_game_update(period=5)))
    result = await broker.submit_prediction("alice", "game-1", "home_win")
    assert isinstance(result, Prediction)
    assert result.window == 4


@pytest.mark.asyncio
async def test_live_game_update_self_heals_scheduled_to_live() -> None:
    """A live game update (period >= 1) promotes SCHEDULED -> LIVE even when the
    one-time GameStartEvent was missed (mid-game join / resume / dropped event),
    so a submission lands in the live window rather than pre-game."""
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))
    assert broker.current_event is not None
    assert broker.current_event.status == EventStatus.SCHEDULED

    # No GameStartEvent — straight to a live 2nd-half update.
    await broker.handle_stream_event(
        _envelope(_game_update(period=2, sport="world_cup"))
    )
    assert broker.current_event.status == EventStatus.LIVE

    result = await broker.submit_prediction("alice", "game-1", "home_win")
    assert isinstance(result, Prediction)
    assert result.window == 2  # 2nd half — not pre-game (window 0)


@pytest.mark.asyncio
async def test_pregame_update_does_not_self_heal_to_live() -> None:
    """A period-0 (pre-game) update must NOT flip the event to LIVE."""
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.handle_stream_event(
        _envelope(_game_update(period=0, sport="world_cup"))
    )
    assert broker.current_event is not None
    assert broker.current_event.status == EventStatus.SCHEDULED


@pytest.mark.asyncio
async def test_compute_elapsed_ratio_nba_quarters() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="nba"))
    )

    # Q2 with 6:00 remaining on a 12:00 quarter → 1*12 + (12-6) = 18 min of 48.
    await broker.handle_stream_event(
        _envelope(_game_update(period=2, game_clock="06:00"))
    )
    ratio = broker._compute_elapsed_ratio()
    assert ratio == pytest.approx(18 / 48, rel=1e-4)


@pytest.mark.asyncio
async def test_compute_elapsed_ratio_ncaa_halves() -> None:
    broker = _broker()
    init = GameInitializeEvent(
        game_id="game-1",
        sport="ncaa",
        home_team="Duke",
        away_team="UNC",
    )
    await broker.handle_stream_event(_envelope(init))
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="ncaa"))
    )

    # H1 with 10:00 left on a 20:00 half → 10 of 40 minutes elapsed.
    update = BaseGameUpdateEvent(
        game_id="game-1",
        sport="ncaa",
        period=1,
        game_clock="10:00",
    )
    await broker.handle_stream_event(_envelope(update))
    ratio = broker._compute_elapsed_ratio()
    assert ratio == pytest.approx(10 / 40, rel=1e-4)


@pytest.mark.asyncio
async def test_compute_elapsed_ratio_unknown_sport_returns_zero() -> None:
    broker = _broker()
    init = GameInitializeEvent(
        game_id="game-1",
        sport="cricket",
        home_team="A",
        away_team="B",
    )
    await broker.handle_stream_event(_envelope(init))
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="cricket"))
    )
    update = BaseGameUpdateEvent(
        game_id="game-1",
        sport="cricket",
        period=1,
        game_clock="01:00",
    )
    await broker.handle_stream_event(_envelope(update))

    assert broker._compute_elapsed_ratio() == 0.0


def test_parse_clock_to_seconds() -> None:
    assert _parse_clock_to_seconds("00:00", 720) == 0
    assert _parse_clock_to_seconds("01:30", 720) == 90
    assert _parse_clock_to_seconds("", 720) == 720
    assert _parse_clock_to_seconds("bogus", 720) == 720
    # Values above the period default are clamped to the default.
    assert _parse_clock_to_seconds("99:00", 720) == 720


def test_parse_soccer_clock_to_elapsed_seconds() -> None:
    assert _parse_soccer_clock_to_elapsed_seconds("45'+2'", 1, 2700) == 47 * 60
    assert _parse_soccer_clock_to_elapsed_seconds("90'+4'", 2, 2700) == 94 * 60
    assert _parse_soccer_clock_to_elapsed_seconds("60:30", 2, 2700) == 60 * 60 + 30
    assert _parse_soccer_clock_to_elapsed_seconds("HT", 1, 2700) == 45 * 60
    assert _parse_soccer_clock_to_elapsed_seconds("FT", 2, 2700) == 90 * 60
    assert _parse_soccer_clock_to_elapsed_seconds("FT", 1, 2700) == 45 * 60
    assert _parse_soccer_clock_to_elapsed_seconds("AET", 1, 2700) == 90 * 60
    assert _parse_soccer_clock_to_elapsed_seconds("AET", 3, 2700) == 90 * 60
    assert _parse_soccer_clock_to_elapsed_seconds("PEN", 4, 2700) == 90 * 60
    assert _parse_soccer_clock_to_elapsed_seconds("FINAL", 0, 2700) == 90 * 60
    assert _parse_soccer_clock_to_elapsed_seconds("", 2, 2700) == 45 * 60
    assert _parse_soccer_clock_to_elapsed_seconds("unknown", 2, 2700) == 45 * 60


@pytest.mark.asyncio
async def test_compute_elapsed_ratio_world_cup_elapsed_clock() -> None:
    broker = _broker()
    init = GameInitializeEvent(
        game_id="game-1",
        sport="world_cup",
        home_team="Argentina",
        away_team="France",
    )
    await broker.handle_stream_event(_envelope(init))
    await broker.handle_stream_event(
        _envelope(GameStartEvent(game_id="game-1", sport="world_cup"))
    )
    update = BaseGameUpdateEvent(
        game_id="game-1",
        sport="world_cup",
        period=2,
        game_clock="60'",
    )
    await broker.handle_stream_event(_envelope(update))

    assert broker._compute_elapsed_ratio() == pytest.approx(60 / 90, rel=1e-4)


@pytest.mark.asyncio
async def test_get_event_info_uses_soccer_score_field() -> None:
    from dojozero.data.world_cup import SoccerTeamMatchStats, WorldCupGameUpdateEvent

    broker = _broker()
    await broker.handle_stream_event(
        _envelope(
            WorldCupGameUpdateEvent(
                game_id="game-1",
                sport="world_cup",
                period=2,
                game_clock="72'",
                home_score=2,
                away_score=1,
                home_team_stats=SoccerTeamMatchStats(
                    team_name="Argentina",
                    score=0,
                ),
                away_team_stats=SoccerTeamMatchStats(
                    team_name="France",
                    score=0,
                ),
            )
        )
    )

    info = await broker.get_event_info()
    assert info is not None
    assert info["home_team"] == "Argentina"
    assert info["away_team"] == "France"
    # Direct event scores are authoritative; stats can lag on soccer feeds.
    assert info["home_score"] == 2
    assert info["away_score"] == 1


def test_extract_score_falls_back_to_soccer_team_stats_score() -> None:
    from dojozero.data.world_cup import SoccerTeamMatchStats

    class SoccerStatsOnlyUpdate:
        home_team_stats = SoccerTeamMatchStats(team_name="Argentina", score=3)
        away_team_stats = SoccerTeamMatchStats(team_name="France", score=2)

    update = cast(Any, SoccerStatsOnlyUpdate())

    assert PredictionBroker._extract_score(update, side="home") == 3
    assert PredictionBroker._extract_score(update, side="away") == 2


# ---------------------------------------------------------------------------
# Checkpoint round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_state_load_state_round_trip() -> None:
    broker = _broker()
    await broker.handle_stream_event(_envelope(_game_init()))
    await broker.submit_prediction("alice", "game-1", "home_win")
    await broker.submit_prediction("bob", "game-1", "away_win")
    await broker.handle_stream_event(_envelope(_game_update(period=2)))

    state = await broker.save_state()

    restored = _broker()
    await restored.load_state(state)

    assert restored.current_event is not None
    assert restored.current_event.event_id == "game-1"
    # Predictions and submitted-windows tracker survive the round trip.
    alice_preds = await restored.get_my_predictions("alice", "game-1")
    bob_preds = await restored.get_my_predictions("bob", "game-1")
    assert len(alice_preds) == 1
    assert len(bob_preds) == 1
    assert alice_preds[0].selection == PredictionOutcome.HOME_WIN
    assert bob_preds[0].selection == PredictionOutcome.AWAY_WIN
