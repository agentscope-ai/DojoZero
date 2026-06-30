from dojozero.arena_server._server import TrialReplayController
from dojozero.arena_server._utils import _compute_replay_meta


def _item(category: str, **data: object) -> dict[str, object]:
    return {"category": category, "data": data}


def test_seek_to_play_index_uses_latest_broker_state_update_snapshot():
    items = [
        _item("agent_initialize"),
        _item("game_initialize"),
        _item("game_start"),
        _item("state_update", accounts_count=2),
        _item("play", period=1, description="play 1"),
        _item("odds_update"),
        _item("play", period=1, description="play 2"),
    ]

    meta = _compute_replay_meta(items, ["play", "game_update"])
    controller = TrialReplayController(
        trial_id="trial-1",
        items=items,
        meta=meta,
        snapshot_size=2,
    )

    snapshot = controller.seek_to_play_index(1)
    categories = [item["category"] for item in snapshot]

    assert "state_update" in categories
    assert categories.count("state_update") == 1


def test_seek_to_play_index_prefers_final_stats_over_state_update():
    items = [
        _item("agent_initialize"),
        _item("game_initialize"),
        _item("game_start"),
        _item("state_update", accounts_count=2),
        _item("play", period=1, description="play 1"),
        _item("state_update", accounts_count=3),
        _item("final_stats", statistics={"alpha": {"net_profit": "1.0"}}),
        _item("odds_update"),
        _item("play", period=1, description="play 2"),
    ]

    meta = _compute_replay_meta(items, ["play", "game_update"])
    controller = TrialReplayController(
        trial_id="trial-1",
        items=items,
        meta=meta,
        snapshot_size=2,
    )

    snapshot = controller.seek_to_play_index(1)
    categories = [item["category"] for item in snapshot]

    assert "final_stats" in categories
    assert "state_update" not in categories


def test_replay_meta_folds_period_zero_into_first_period():
    items = [
        _item("game_update", period=0, game_clock="0'", home_score=0, away_score=0),
        _item("play", period=0, game_clock="0'", description="pregame touch"),
        _item("game_update", period=1, game_clock="1'", home_score=0, away_score=0),
        _item("play", period=1, game_clock="1'", description="first half"),
        _item("game_update", period=2, game_clock="46'", home_score=0, away_score=0),
        _item("play", period=2, game_clock="46'", description="second half"),
    ]

    meta = _compute_replay_meta(items, ["play", "game_update"])

    assert [(p.period, p.start_play_index, p.play_count) for p in meta.periods] == [
        (1, 0, 4),
        (2, 4, 2),
    ]
