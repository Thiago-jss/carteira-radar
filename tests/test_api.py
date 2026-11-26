import hashlib
import json

from fastapi.testclient import TestClient

from carteira_radar.api import create_app

RUN = "a" * 32


def record(entity, month, probability, label=0):
    return {
        "entity_id": entity,
        "name": f"Carteira {entity}",
        "month": month,
        "status": "scored" if probability is not None else "unscored",
        "probability": probability,
        "ratio": 0.03,
        "delta_3": 0.01,
        "label": label,
        "outcome_ratio": 0.04 if label is not None else None,
        "reason": "insufficient_history" if probability is None else None,
        "cohort": "heldout",
        "explanation": None if probability is None else {"base_log_odds": 0, "contributions": {}},
    }


def snapshot(root, rows):
    run = root / "runs" / RUN
    run.mkdir(parents=True)
    (root / "CURRENT").write_text(RUN)
    checksums = {}
    for filename, data in [
        ("benchmark.json", {"selected_model": "logistic"}),
        ("predictions.json", {"predictions": rows}),
    ]:
        raw = json.dumps(data).encode()
        (run / filename).write_bytes(raw)
        checksums[filename] = hashlib.sha256(raw).hexdigest()
    (run / "manifest.json").write_text(json.dumps({"run_id": RUN, "sha256": checksums}))
    return run


def test_rank_pagination_and_unscored_are_not_zero_risk(tmp_path):
    snapshot(
        tmp_path,
        [
            record("B", "2025-05", None),
            record("A", "2025-05", 0.0),
            record("C", "2025-05", 0.9),
            record("C", "2025-04", 0.12),
        ],
    )
    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/api/v1/predictions?limit=2")
        assert response.status_code == 200
        page = response.json()
        assert (page["month"], page["total"], page["scored"]) == ("2025-05", 3, 2)
        assert [r["entity_id"] for r in page["predictions"]] == ["C", "A"]
        assert page["predictions"][1]["probability"] == 0.0
        last = client.get("/api/v1/predictions?offset=2").json()["predictions"]
        assert last[0]["probability"] is None and last[0]["status"] == "unscored"
        assert response.headers["cache-control"] == "no-store"
        history = client.get("/api/v1/entities/C").json()["history"]
        assert [r["month"] for r in history] == ["2025-04", "2025-05"]
        assert client.get("/api/v1/predictions?month=2025-13").status_code == 422
        assert client.get("/api/v1/predictions?month=2024-01").status_code == 404
        assert client.get("/api/v1/entities/NO").status_code == 404


def test_snapshot_corruption_fails_closed(tmp_path):
    directory = snapshot(tmp_path, [record("A", "2025-05", 0.5)])
    with (directory / "predictions.json").open("a") as handle:
        handle.write(" ")
    assert TestClient(create_app(tmp_path)).get("/health/ready").status_code == 503


def test_duplicate_id_month_fails_closed_even_with_valid_checksums(tmp_path):
    row = record("A", "2025-05", 0.5)
    snapshot(tmp_path, [row, row])
    assert TestClient(create_app(tmp_path)).get("/health/ready").status_code == 503


def test_pointer_cannot_select_an_arbitrary_path(tmp_path):
    (tmp_path / "CURRENT").write_text("../../private")
    response = TestClient(create_app(tmp_path)).get("/api/v1/benchmark")
    assert response.status_code == 503
    assert "../../private" not in response.text
