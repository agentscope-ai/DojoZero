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
