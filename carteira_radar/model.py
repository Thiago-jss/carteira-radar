"""Frozen, out-of-time credit deterioration benchmark (batch only)."""

import hashlib
import json
import math
import os
import platform
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import sklearn
import xgboost as xgb
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

FEATURES = ["ratio", "lag_1", "lag_3", "delta_3", "log_balance"]
WINDOW = 6
HORIZON = 3
PARAMETERS = {
    "momentum": {"score": "delta_3"},
    "logistic": {"C": 1.0, "solver": "lbfgs", "max_iter": 1000, "random_state": 42},
    "xgboost": {
        "n_estimators": 120,
        "max_depth": 3,
        "learning_rate": 0.05,
        "min_child_weight": 3,
        "subsample": 1.0,
        "colsample_bytree": 1.0,
        "reg_lambda": 1.0,
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "tree_method": "hist",
        "n_jobs": 1,
        "random_state": 42,
    },
}


def _month(value):
    return None if pd.isna(value) else value.strftime("%Y-%m")


def _number(value):
    return None if pd.isna(value) or not math.isfinite(float(value)) else float(value)


def _describe(rows, labeled, start, end):
    return {
        "start": _month(start),
        "end": _month(end),
        "months": [_month(m) for m in rows.month.drop_duplicates()],
        "rows": len(rows),
        "labeled_complete_rows": len(labeled),
        "last_label_month": _month(labeled.label_month.max()),
    }


def _metrics(rows, scores, probability):
    y = rows.label.to_numpy(dtype=int)
    positives = int(y.sum())
    ranked = rows[["month", "entity_id"]].assign(score=scores, label=y)
    monthly = []
    for month, group in ranked.groupby("month", sort=True):
        k = (len(group) + 9) // 10
        top = group.sort_values(["score", "entity_id"], ascending=[False, True]).head(k)
        hits, positive = int(top.label.sum()), int(group.label.sum())
        monthly.append(
            {
                "month": _month(month),
                "n": len(group),
                "k": k,
                "positives": positive,
                "precision": hits / k,
                "recall": hits / positive if positive else None,
            }
        )
    recalls = [m["recall"] for m in monthly if m["recall"] is not None]
    return {
        "n": len(y),
        "positives": positives,
        "average_precision": float(average_precision_score(y, scores)) if positives else None,
        "brier": float(brier_score_loss(y, scores)) if probability and len(y) else None,
        "precision_at_10pct": float(np.mean([m["precision"] for m in monthly]))
        if monthly
        else None,
        "recall_at_10pct": float(np.mean(recalls)) if recalls else None,
        "by_month": monthly,
    }


def _tracking(output, report):
    # Never use a globally configured tracking server or artifact destination.
    from mlflow import __version__
    from mlflow.tracking import MlflowClient

    root = (output / "mlflow").resolve()
    root.mkdir(exist_ok=True)
    client = MlflowClient(tracking_uri=root.as_uri())
    experiment = client.get_experiment_by_name("carteira-radar")
    artifact_uri = (root / "artifacts").as_uri()
    if experiment and experiment.artifact_location != artifact_uri:
        raise ValueError("Expected a local Carteira Radar MLflow artifact store")
    experiment_id = (
        experiment.experiment_id
        if experiment
        else client.create_experiment("carteira-radar", artifact_location=artifact_uri)
    )
    run_id = client.create_run(experiment_id).info.run_id
    try:
        client.log_param(run_id, "protocol", report["protocol"])
        if report["selected"] is not None:
            client.log_param(run_id, "selected", report["selected"])
        for name, model in report["models"].items():
            for split in ("validation", "test"):
                for metric in (
                    "average_precision",
                    "brier",
                    "precision_at_10pct",
                    "recall_at_10pct",
                ):
                    value = model.get(split, {}).get(metric)
                    if value is not None:
                        client.log_metric(run_id, f"{name}.{split}.{metric}", value)
        report["tracking"] = {"status": "logged", "run_id": run_id, "mlflow_version": __version__}
        client.log_dict(run_id, report, "benchmark.json")
    except Exception:
        client.set_terminated(run_id, "FAILED")
        raise
    client.set_terminated(run_id, "FINISHED")


def run_benchmark(frame: pd.DataFrame, output: Path) -> dict:
    """Fit once before validation; select on validation; score untouched test months."""
    frame = frame.copy()
    frame["entity_id"] = frame.entity_id.astype(str)
    frame = frame.sort_values(["month", "entity_id"], ignore_index=True)
    if frame.duplicated(["month", "entity_id"]).any():
        raise ValueError("Expected unique entity_id/month rows")
    if (frame.label_month != frame.month + pd.DateOffset(months=HORIZON)).any():
        raise ValueError("label_month must be exactly month + 3 calendar months")
    if not frame.ratio.between(0, 1).fillna(False).all():
        raise ValueError("A finite current ratio in [0, 1] is required by the snapshot schema")
    complete = pd.Series(
        np.isfinite(frame[FEATURES].to_numpy(dtype=float, na_value=np.nan)).all(axis=1),
        index=frame.index,
    )
    labeled = complete & frame.label.isin([0, 1])
    as_of = frame.month.max()
    test_end = as_of - pd.DateOffset(months=HORIZON)
    test_start = test_end - pd.DateOffset(months=WINDOW - 1)
    val_end = test_start - pd.DateOffset(months=HORIZON + 1)
    val_start = val_end - pd.DateOffset(months=WINDOW - 1)
    train_mask = frame.label_month < val_start
    val_mask = frame.month.between(val_start, val_end) & (frame.label_month < test_start)
    test_mask = frame.month.between(test_start, test_end) & (frame.label_month <= as_of)
    train, val, test = [frame.loc[mask & labeled] for mask in (train_mask, val_mask, test_mask)]
    snapshots = frame.loc[test_mask]
    release = frame.loc[test_mask | frame.month.eq(as_of)]
    report: dict[str, Any] = {
        "protocol": "frozen-temporal-v2",
        "status": "insufficient_data",
        "selected": None,
        "selected_model": None,
        "selection_rule": "Maximum validation AP among probabilistic candidates; logistic wins ties",
        "as_of": _month(as_of),
        "features": FEATURES.copy(),
        "versions": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "sklearn": sklearn.__version__,
            "xgboost": xgb.__version__,
        },
        "tracking": {"status": "disabled"},
        "splits": {
            "train": _describe(
                frame.loc[train_mask],
                train,
                frame.loc[train_mask, "month"].min(),
                val_start - pd.DateOffset(months=HORIZON + 1),
            ),
            "validation": _describe(frame.loc[val_mask], val, val_start, val_end),
            "test": _describe(snapshots, test, test_start, test_end),
        },
        "models": {
            name: {"parameters": params.copy(), "probability": name != "momentum"}
            for name, params in PARAMETERS.items()
        },
        "comparisons": {},
        "reason": None,
        "model_id": None,
    }
    predictions = {}
    explanations = {}
    if train.label.nunique() < 2 or val.label.nunique() < 2 or snapshots.month.nunique() < 3:
        report["reason"] = (
            "Need both classes in complete mature training and validation, "
            "and at least three observed test months"
        )
    else:
        estimators = {
            "logistic": make_pipeline(
                StandardScaler(), LogisticRegression(**PARAMETERS["logistic"])
            ),
            "xgboost": xgb.XGBClassifier(**PARAMETERS["xgboost"]),
        }
        for estimator in estimators.values():
            estimator.fit(train[FEATURES], train.label.astype(int))
        for name, model in report["models"].items():
            scores = (
                val.delta_3.to_numpy()
                if name == "momentum"
                else estimators[name].predict_proba(val[FEATURES])[:, 1]
            )
            model["validation"] = _metrics(val, scores, model["probability"])
        winner = max(
            estimators, key=lambda name: report["models"][name]["validation"]["average_precision"]
        )
        report["selected"] = winner
        report["selected_model"] = winner
        # The family and fit are locked before held-out labels enter metric calculation.
        eligible = release.loc[complete.loc[release.index]]
        if not eligible.empty:
            estimator = estimators[winner]
            features = eligible[FEATURES]
            predictions = dict(zip(eligible.index, estimator.predict_proba(features)[:, 1]))
            if winner == "xgboost":
                booster = estimator.get_booster()
                components = booster.predict(xgb.DMatrix(features), pred_contribs=True)
            else:
                scaler, regression = estimator.steps[0][1], estimator.steps[1][1]
                impacts = scaler.transform(features) * regression.coef_[0]
                components = np.column_stack(
                    (impacts, np.full(len(impacts), regression.intercept_[0]))
                )
            logits = components.sum(axis=1)
            reconstructed = 1 / (1 + np.exp(-np.clip(logits, -700, 700)))
            if not np.allclose(reconstructed, list(predictions.values()), atol=1e-6):
                raise ValueError("Model contributions do not reconstruct the published score")
            explanations = dict(zip(eligible.index, components))
        for name, model in report["models"].items():
            if test.empty or name == "momentum":
                scores = test.delta_3.to_numpy()
            else:
                scores = estimators[name].predict_proba(test[FEATURES])[:, 1]
            model["test"] = _metrics(test, scores, model["probability"])
        for split in ("validation", "test"):
            baseline = report["models"]["momentum"][split]["average_precision"]
            selected_ap = report["models"][winner][split]["average_precision"]
            beaters = [
                n
                for n in estimators
                if baseline is not None
                and report["models"][n][split]["average_precision"] > baseline
            ]
            report["comparisons"][split] = {
                "selected_ap_minus_momentum": selected_ap - baseline
                if baseline is not None
                else None,
                "models_above_momentum_ap": beaters,
                "conclusion": (
                    "AP unavailable"
                    if baseline is None
                    else "At least one candidate beats momentum on AP"
                    if beaters
                    else "No model beats momentum on AP"
                ),
            }
        identity = {
            "protocol": report["protocol"],
            "selected": winner,
            "parameters": PARAMETERS[winner],
            "features": FEATURES,
            "versions": report["versions"],
            "training_sha256": hashlib.sha256(
                train[["month", "entity_id", *FEATURES, "label"]].to_csv(index=False).encode()
            ).hexdigest(),
        }
        report["model_id"] = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest()
        report["status"] = "ok"
    records = []
    for row in release.itertuples():
        prediction = _number(predictions.get(row.Index))
        is_holdout = row.month != as_of
        impacts = explanations.get(row.Index)
        reason = (
            None
            if prediction is not None
            else (
                "missing_or_nonfinite_features"
                if not complete.loc[row.Index]
                else "benchmark_unavailable"
            )
        )
        records.append(
            {
                "entity_id": row.entity_id,
                "name": str(row.name),
                "month": _month(row.month),
                "status": "scored" if prediction is not None else "unscored",
                "probability": prediction,
                "ratio": _number(row.ratio),
                "delta_3": _number(row.delta_3),
                "label": int(row.label) if is_holdout and pd.notna(row.label) else None,
                "outcome_ratio": _number(row.outcome_ratio) if is_holdout else None,
                "reason": reason,
                "cohort": "heldout" if is_holdout else "latest_in_snapshot",
                "explanation": None
                if impacts is None
                else {
                    "base_log_odds": float(impacts[-1]),
                    "contributions": dict(zip(FEATURES, map(float, impacts[:-1]))),
                },
            }
        )
    output.mkdir(parents=True, exist_ok=True)
    if os.environ.get("CARTEIRA_RADAR_MLFLOW") == "1":
        _tracking(output, report)
    for path, data in [("benchmark.json", report), ("predictions.json", {"predictions": records})]:
        (output / path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    return report
