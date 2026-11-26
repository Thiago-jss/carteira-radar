import json
import math

import pandas as pd

from carteira_radar.fixture import synthetic_frame
from carteira_radar.model import run_benchmark


def panel():
    rows = []
    for j, month in enumerate(pd.date_range("2022-01-01", periods=48, freq="MS")):
        for i in range(20):
            ratio = 0.12 + 0.01 * ((i + j) % 9)
            future = 0.12 + 0.01 * ((i + j + 3) % 9)
            rows.append(
                {
                    "entity_id": str(i),
                    "name": f"Entity {i}",
                    "month": month,
                    "ratio": ratio,
                    "lag_1": 0.12 + 0.01 * ((i + j - 1) % 9),
                    "lag_3": 0.12 + 0.01 * ((i + j - 3) % 9),
                    "delta_3": ratio - (0.12 + 0.01 * ((i + j - 3) % 9)),
                    "log_balance": 8 + i / 20,
                    "label": int(future - ratio > 0.01999) if j < 45 else None,
                    "outcome_ratio": future if j < 45 else None,
                    "label_month": month + pd.DateOffset(months=3),
                }
            )
    return pd.DataFrame(rows)


def test_frozen_temporal_benchmark(tmp_path):
    frame = panel()
    report = run_benchmark(frame, tmp_path)
    assert report == json.loads((tmp_path / "benchmark.json").read_text())
    assert report["status"] == "ok"
    assert report["selected_model"] == report["selected"]
    splits = report["splits"]
    assert splits["train"]["last_label_month"] < splits["validation"]["start"]
    assert splits["validation"]["last_label_month"] < splits["test"]["start"]
    assert len(splits["test"]["months"]) >= 3
    assert set(report["models"]) == {"momentum", "logistic", "xgboost"}
    assert report["models"]["momentum"]["test"]["brier"] is None
    changed = frame.copy()
    mask = changed.month.dt.strftime("%Y-%m") >= splits["test"]["start"]
    changed.loc[mask & changed.label.notna(), "label"] = (
        1 - changed.loc[mask & changed.label.notna(), "label"]
    )
    other = run_benchmark(changed, tmp_path / "changed")
    assert report["selected"] == other["selected"]
    first = json.loads((tmp_path / "predictions.json").read_text())["predictions"]
    second = json.loads((tmp_path / "changed/predictions.json").read_text())["predictions"]
    assert [(r["month"], r["entity_id"], r["probability"]) for r in first] == [
        (r["month"], r["entity_id"], r["probability"]) for r in second
    ]


def test_absent_test_outcomes_do_not_change_scores(tmp_path):
    frame = panel()
    original = run_benchmark(frame, tmp_path)
    first = json.loads((tmp_path / "predictions.json").read_text())["predictions"]
    frame.loc[
        frame.month.dt.strftime("%Y-%m") >= original["splits"]["test"]["start"],
        ["label", "outcome_ratio"],
    ] = None
    report = run_benchmark(frame, tmp_path)
    second = json.loads((tmp_path / "predictions.json").read_text())["predictions"]
    assert report["selected"] == original["selected"]
    assert report["models"][report["selected"]]["test"]["average_precision"] is None
    assert [r["probability"] for r in second] == [r["probability"] for r in first]
    assert all(r["label"] is None for r in second)


def test_missing_features_abstain_and_metrics_use_common_rows(tmp_path):
    frame = panel()
    frame.loc[frame.month.eq("2025-04-01") & frame.entity_id.eq("0"), "delta_3"] = None
    report = run_benchmark(frame, tmp_path)
    snapshot = json.loads((tmp_path / "predictions.json").read_text())["predictions"]
    assert snapshot == sorted(snapshot, key=lambda row: (row["month"], row["entity_id"]))
    (row,) = [r for r in snapshot if r["status"] == "unscored"]
    assert row["entity_id"] == "0" and row["month"] == "2025-04"
    assert row["probability"] is None and row["delta_3"] is None
    assert row["reason"] == "missing_or_nonfinite_features"
    assert row["label"] in (0, 1) and row["outcome_ratio"] is not None
    scoreable = [r for r in snapshot if r["status"] == "scored"]
    latest = [r for r in scoreable if r["cohort"] == "latest_in_snapshot"]
    assert latest and all(r["label"] is None and r["outcome_ratio"] is None for r in latest)
    for record in scoreable:
        explanation = record["explanation"]
        assert set(explanation["contributions"]) == set(report["features"])
        logit = explanation["base_log_odds"] + sum(explanation["contributions"].values())
        assert abs((1 / (1 + math.exp(-logit))) - record["probability"]) < 1e-6
    scored = [r for r in scoreable if r["cohort"] == "heldout"]
    metric = report["models"][report["selected"]]["test"]
    momentum = report["models"]["momentum"]["test"]
    assert momentum["n"] == metric["n"] == len(scored)
    expected_brier = sum((r["probability"] - r["label"]) ** 2 for r in scored) / len(scored)
    assert abs(metric["brier"] - expected_brier) < 1e-12
    for month in metric["by_month"]:
        ranked = sorted(
            (r for r in scored if r["month"] == month["month"]),
            key=lambda r: (-r["probability"], r["entity_id"]),
        )
        assert month["k"] == 2
        hits = sum(r["label"] for r in ranked[:2])
        assert month["precision"] == hits / 2
        assert month["recall"] == hits / sum(r["label"] for r in ranked)


def test_shared_fixture_logs_metrics_and_report_locally(tmp_path, monkeypatch):
    from mlflow.tracking import MlflowClient

    monkeypatch.setenv("CARTEIRA_RADAR_MLFLOW", "1")
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://example.invalid")
    report = run_benchmark(synthetic_frame(), tmp_path)
    assert report["status"] == "ok"
    client = MlflowClient(tracking_uri=(tmp_path / "mlflow").as_uri())
    run = client.get_run(report["tracking"]["run_id"])
    assert run.info.status == "FINISHED"
    selected = report["selected"]
    assert run.data.metrics[f"{selected}.test.brier"] == report["models"][selected]["test"]["brier"]
    artifact = client.download_artifacts(run.info.run_id, "benchmark.json", str(tmp_path))
    with open(artifact, encoding="utf-8") as file:
        assert json.load(file) == report
