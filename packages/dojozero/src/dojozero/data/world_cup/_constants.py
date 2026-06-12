"""Shared constants for FIFA soccer data integrations."""

from __future__ import annotations


WORLD_CUP_KNOWN_LEAGUES: set[str] = {
    "fifa.world",  # Men's World Cup
    "fifa.wwc",  # Women's World Cup
    "fifa.cwc",  # Club World Cup
    "fifa.worldq.uefa",
    "fifa.worldq.concacaf",
    "fifa.worldq.conmebol",
    "fifa.worldq.afc",
    "fifa.worldq.caf",
    "fifa.worldq.ofc",
}


# ESPN soccer status name -> canonical DojoZero status code.
# Values follow the scheduler convention:
# 1=scheduled, 2=in_progress, 3=final, 4=postponed, 5=cancelled.
SOCCER_STATUS_NAME_MAP: dict[str, int] = {
    "STATUS_SCHEDULED": 1,
    "STATUS_DELAYED": 1,
    "STATUS_POSTPONED": 4,
    "STATUS_FIRST_HALF": 2,
    "STATUS_HALFTIME": 2,
    "STATUS_SECOND_HALF": 2,
    "STATUS_IN_PROGRESS": 2,
    "STATUS_END_PERIOD": 2,
    "STATUS_EXTRA_TIME": 2,
    "STATUS_END_EXTRA_TIME": 2,
    "STATUS_SHOOTOUT": 2,
    "STATUS_FULL_TIME": 3,
    "STATUS_FINAL": 3,
    "STATUS_FINAL_AET": 3,
    "STATUS_FINAL_PEN": 3,
    "STATUS_ABANDONED": 5,
    "STATUS_CANCELED": 5,
    "STATUS_CANCELLED": 5,
    "STATUS_FORFEIT": 5,
}


__all__ = ["SOCCER_STATUS_NAME_MAP", "WORLD_CUP_KNOWN_LEAGUES"]
