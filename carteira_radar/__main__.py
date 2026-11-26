"""Reproducible research commands and atomic artifact publication."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path


def publish(args):
    from .api import Snapshot
    from .model import run_benchmark

    if args.fixture:
        from .fixture import synthetic_frame

        frame, metadata = synthetic_frame(), {"source": "synthetic", "point_in_time": False}
    else:
        from .data import build_dataset

        frame, metadata = build_dataset(args.raw, args.start, args.end)
    output = args.output.resolve()
    collection = output / "runs"
    collection.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=".building-", dir=collection) as temporary:
        stage = Path(temporary) / "result"
        stage.mkdir()
        if args.track:
            import mlflow

            tracking = f"sqlite:///{(output / 'tracking.sqlite').as_posix()}"
            artifact_location = (output / "mlartifacts").as_uri()
            mlflow.set_tracking_uri(tracking)
            experiment = mlflow.get_experiment_by_name("carteira-radar")
            experiment_id = (
                experiment.experiment_id
                if experiment
                else mlflow.create_experiment("carteira-radar", artifact_location)
            )
            with mlflow.start_run(run_name=run_id, experiment_id=experiment_id):
                mlflow.log_params(
                    {
                        "snapshot_id": run_id,
                        "data_kind": "fixture" if args.fixture else "cvm",
                        "rows": len(frame),
                        "start": str(frame.month.min().date()),
                        "end": str(frame.month.max().date()),
                    }
                )
                result = run_benchmark(frame, stage)
                mlflow.log_params(
                    {
                        "protocol": result["protocol"],
                        "selected": result["selected"],
                        "model_id": result["model_id"],
                    }
                )
                for name, model in result["models"].items():
                    for split in ("validation", "test"):
                        for metric in (
                            "average_precision",
                            "precision_at_10pct",
                            "recall_at_10pct",
                            "brier",
                        ):
                            value = (model.get(split) or {}).get(metric)
                            if value is not None:
                                mlflow.log_metric(f"{name}.{split}.{metric}", value)
                mlflow.log_artifact(str(stage / "benchmark.json"))
        else:
            result = run_benchmark(frame, stage)
        safe_features = stage / "features.parquet"
        frame.to_parquet(safe_features, index=False)
        import duckdb

        with duckdb.connect() as db:
            (distinct, count, with_labels) = db.execute(
                "SELECT count(DISTINCT (entity_id, month)), count(*), count(label) FROM read_parquet(?)",
                [str(safe_features)],
            ).fetchone()
        if not count or distinct != count:
            raise ValueError("Dataset is empty or duplicates the entity-month key")
        digests = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (safe_features, stage / "benchmark.json", stage / "predictions.json")
        }
        from importlib.metadata import version

        manifest = {
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "kind": "synthetic" if args.fixture else "cvm_revised_history",
            "point_in_time": False,
            "rows": count,
            "observed_labels": with_labels,
            "data": metadata,
            "sha256": digests,
            "versions": {
                name: version(name) for name in ("pandas", "scikit-learn", "duckdb", "pyarrow")
            },
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
        destination = collection / run_id
        stage.rename(destination)
        try:
            test_root = Path(temporary) / "verify"
            test_root.mkdir()
            (test_root / "CURRENT").write_text(run_id, encoding="ascii")
            (test_root / "runs").symlink_to(collection, target_is_directory=True)
            _ = Snapshot(test_root).contents
        except Exception:
            import shutil

            shutil.rmtree(destination)
            raise
        pointer = output / f".CURRENT-{run_id}"
        pointer.write_text(run_id + "\n", encoding="ascii")
        os.replace(pointer, output / "CURRENT")
    print(
        json.dumps(
            {
                "run_id": run_id,
                "rows": count,
                "observed_labels": with_labels,
                "selected_model": result.get("selected_model"),
            },
            ensure_ascii=False,
        )
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Carteira Radar offline experiment")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser(
        "build",
        help="Build and verify a snapshot without touching the active version until completion",
    )
    build.add_argument("--raw", type=Path, default=Path("data/raw"))
    build.add_argument("--output", type=Path, default=Path("artifacts"))
    build.add_argument("--start", default="2022-01")
    build.add_argument("--end", default="2026-06")
    build.add_argument("--fixture", action="store_true", help="Synthetic data for tests only")
    build.add_argument(
        "--track", action="store_true", help="Log experiment to embedded MLflow SQLite store"
    )
    args = parser.parse_args(argv)
    try:
        publish(args)
    except (OSError, ValueError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
