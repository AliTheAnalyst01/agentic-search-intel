"""Opportunity score.

The formula is ours to define. It answers: how worthwhile is it to go
after this query? Three components, weighted to sum to 1.0 so the result
is always in [0, 1]:

  volume    (0.35) - log-scaled, because the difference between 100 and
                     1,000 searches matters more than 10,000 vs 11,000
  ease      (0.25) - inverse of competitive difficulty
  gap       (0.40) - heaviest weight: a query you already rank #1 for is
                     not an opportunity, however big it is

Unknown visibility scores a neutral 0.5 on the gap component rather than
1.0, so a failed check never inflates a score into a false priority.
"""

import math

WEIGHT_VOLUME = 0.35
WEIGHT_EASE = 0.25
WEIGHT_GAP = 0.40

# Volumes above this are treated as equivalently large.
VOLUME_CEILING = 100_000


def volume_component(search_volume: int) -> float:
    if search_volume <= 0:
        return 0.0
    scaled = math.log10(1 + search_volume) / math.log10(1 + VOLUME_CEILING)
    return min(1.0, scaled)


def ease_component(difficulty: int) -> float:
    clamped = max(0, min(100, difficulty))
    return 1.0 - (clamped / 100)


def gap_component(
    visibility_status: str, visibility_position: int | None
) -> float:
    """How much room there is to improve."""
    if visibility_status == "unknown":
        return 0.5
    if visibility_status == "not_visible":
        return 1.0

    position = visibility_position or 100
    if position <= 3:
        return 0.0
    if position <= 10:
        return 0.3
    return 0.6


def opportunity_score(
    *,
    search_volume: int,
    difficulty: int,
    visibility_status: str,
    visibility_position: int | None = None,
) -> float:
    score = (
        WEIGHT_VOLUME * volume_component(search_volume)
        + WEIGHT_EASE * ease_component(difficulty)
        + WEIGHT_GAP * gap_component(visibility_status, visibility_position)
    )
    return round(score, 3)
