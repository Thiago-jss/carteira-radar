"""Deterministic fabricated panel for automated checks; never evidence of quality."""

import numpy as np
import pandas as pd


def synthetic_frame() -> pd.DataFrame:
    rng = np.random.default_rng(51)
    months = pd.date_range("2022-01-01", "2026-06-01", freq="MS")
    rows = []
    for i in range(35):
        previous = 0.08 + i / 500
        for month in months:
            previous = float(
                np.clip(0.62 * previous + 0.38 * (0.08 + i / 500) + rng.normal(0, 0.033), 0, 0.9)
            )
            rows.append(
                {
                    "entity_id": f"FICTICIO:{i:02d}",
                    "name": f"Carteira fictícia {i:02d}",
                    "month": month,
                    "ratio": previous,
                    "log_balance": 15.0 + i / 8,
                }
            )
    frame = pd.DataFrame(rows).sort_values(["entity_id", "month"])
    group = frame.groupby("entity_id")["ratio"]
    frame["lag_1"] = group.shift(1)
    frame["lag_3"] = group.shift(3)
    frame["delta_3"] = frame.ratio - frame.lag_3
    frame["outcome_ratio"] = group.shift(-3)
    frame["label"] = (frame.outcome_ratio - frame.ratio >= 0.02).astype("Int8")
    frame.loc[frame.outcome_ratio.isna(), "label"] = pd.NA
    frame["label_month"] = frame.month + pd.DateOffset(months=3)
    return frame.reset_index(drop=True)
