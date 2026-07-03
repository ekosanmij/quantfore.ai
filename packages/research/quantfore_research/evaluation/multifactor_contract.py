"""Frozen date boundaries shared by the Sprint 8 evaluation pipeline."""

from datetime import date


HOLDOUT_START = date(2022, 1, 1)
HOLDOUT_END = date(2025, 6, 30)
HOLDOUT_START_TEXT = HOLDOUT_START.isoformat()
HOLDOUT_END_TEXT = HOLDOUT_END.isoformat()


def reject_after_frozen_cutoff(value: date) -> None:
    """Refuse cohorts whose longest outcome is not mature for this contract."""

    if value > HOLDOUT_END:
        raise ValueError(
            f"evaluation date {value.isoformat()} exceeds frozen cutoff "
            f"{HOLDOUT_END_TEXT}"
        )
