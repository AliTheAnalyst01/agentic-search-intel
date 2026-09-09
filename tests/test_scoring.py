from app.nodes.scoring import (
    ease_component,
    gap_component,
    opportunity_score,
    volume_component,
)


def test_score_is_bounded():
    high = opportunity_score(
        search_volume=100_000, difficulty=0, visibility_status="not_visible"
    )
    low = opportunity_score(
        search_volume=0, difficulty=100, visibility_status="visible", visibility_position=1
    )

    assert high == 1.0
    assert low == 0.0


def test_volume_is_log_scaled():
    small_gain = volume_component(1000) - volume_component(100)
    large_gain = volume_component(11_000) - volume_component(10_000)

    assert small_gain > large_gain


def test_zero_volume_scores_nothing():
    assert volume_component(0) == 0.0


def test_ease_is_inverse_of_difficulty():
    assert ease_component(0) == 1.0
    assert ease_component(100) == 0.0
    assert ease_component(150) == 0.0, "clamped"


def test_ranking_first_leaves_no_gap():
    assert gap_component("visible", 1) == 0.0


def test_not_ranking_is_a_full_gap():
    assert gap_component("not_visible", None) == 1.0


def test_unknown_visibility_is_neutral_not_optimistic():
    """A failed check must not inflate a score into a false priority."""
    assert gap_component("unknown", None) == 0.5
    assert gap_component("unknown", None) < gap_component("not_visible", None)


def test_high_volume_low_difficulty_gap_outranks_the_reverse():
    good = opportunity_score(
        search_volume=8100, difficulty=28, visibility_status="not_visible"
    )
    poor = opportunity_score(
        search_volume=200, difficulty=90, visibility_status="visible", visibility_position=2
    )

    assert good > poor


# --- unmeasured metrics ---

def test_unmeasured_volume_is_neutral():
    assert volume_component(None) == 0.5


def test_measured_zero_differs_from_unmeasured():
    """Zero searches is a finding; unknown is an absence of one."""
    assert volume_component(0) == 0.0
    assert volume_component(None) == 0.5


def test_unmeasured_difficulty_does_not_earn_perfect_ease():
    """difficulty=0 means trivially easy; None means we never checked."""
    assert ease_component(0) == 1.0
    assert ease_component(None) == 0.5
    assert ease_component(None) < ease_component(0)


def test_unmeasured_query_scores_below_a_measured_easy_one():
    unmeasured = opportunity_score(
        search_volume=None, difficulty=None, visibility_status="not_visible"
    )
    measured_easy = opportunity_score(
        search_volume=8100, difficulty=10, visibility_status="not_visible"
    )
    assert unmeasured < measured_easy
