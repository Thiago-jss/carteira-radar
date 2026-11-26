"""Read-only HTTP access to an offline scoring snapshot; never trains on a request."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

MONTH = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
ENTITY = r"^[A-Za-z0-9./:_-]{1,80}$"
RUN_ID = r"^[0-9a-f]{32}$"
MAX_SNAPSHOT_BYTES = 32_000_000


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    base_log_odds: float
    contributions: dict[Literal["ratio", "lag_1", "lag_3", "delta_3", "log_balance"], float]


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    entity_id: str = Field(pattern=ENTITY)
    name: str = Field(min_length=1, max_length=300)
    month: str = Field(pattern=MONTH)
    status: Literal["scored", "unscored"]
    probability: float | None = Field(ge=0, le=1)
    ratio: float = Field(ge=0, le=1)
    delta_3: float | None = Field(ge=-1, le=1)
    label: Literal[0, 1] | None
    outcome_ratio: float | None = Field(ge=0, le=1)
    reason: str | None = Field(max_length=250)
    cohort: Literal["heldout", "latest_in_snapshot"]
    explanation: Explanation | None

    @model_validator(mode="after")
    def coherent_score(self):
        if self.status == "scored" and (
            self.probability is None or self.reason is not None or self.explanation is None
        ):
            raise ValueError("A scored record requires a probability and no abstention reason")
        if self.status == "unscored" and (
            self.probability is not None or not self.reason or self.explanation is not None
        ):
            raise ValueError("An unscored record requires an abstention reason and no probability")
        if (self.label is None) != (self.outcome_ratio is None):
            raise ValueError("The outcome and its label must be present or absent together")
        if self.cohort == "latest_in_snapshot" and self.label is not None:
            raise ValueError("Latest snapshot does not contain a mature outcome")
        return self


class PredictionPage(BaseModel):
    month: str
    months: list[str]
    total: int
    scored: int
    offset: int
    predictions: list[Prediction]


class EntityHistory(BaseModel):
    entity_id: str
    history: list[Prediction]


def load_json(path: Path, expected_sha256: str | None = None):
    with path.open("rb") as handle:
        content = handle.read(MAX_SNAPSHOT_BYTES + 1)
    if len(content) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot exceeds the runtime size budget")
    if expected_sha256 is not None and hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("Snapshot does not match its recorded checksum")

    def reject_constant(value: str):
        raise ValueError(f"Invalid JSON constant {value}")

    return json.loads(content, parse_constant=reject_constant)


class Snapshot:
    def __init__(self, root: Path):
        self.root = root

    @cached_property
    def contents(self):
        with (self.root / "CURRENT").open(encoding="ascii") as handle:
            run = handle.read(128).strip()
        if not re.fullmatch(RUN_ID, run):
            raise ValueError("Malformed snapshot pointer")
        directory = self.root / "runs" / run
        manifest = load_json(directory / "manifest.json")
        if not isinstance(manifest, dict) or manifest.get("run_id") != run:
            raise ValueError("Snapshot provenance mismatch")
        checksums = manifest.get("sha256")
        names = ("benchmark.json", "predictions.json")
        if not isinstance(checksums, dict) or any(
            not isinstance(checksums.get(name), str)
            or not re.fullmatch(r"[a-f0-9]{64}", checksums[name])
            for name in names
        ):
            raise ValueError("Missing JSON snapshot checksums")
        report = load_json(directory / names[0], checksums[names[0]])
        if not isinstance(report, dict):
            raise TypeError("Expected an object benchmark")
        payload = load_json(directory / names[1], checksums[names[1]])
        if not isinstance(payload, dict) or "predictions" not in payload:
            raise ValueError("Expected a prediction list")
        rows = TypeAdapter(list[Prediction]).validate_python(payload["predictions"])
        if not rows or len(rows) > 250_000:
            raise ValueError("Prediction inventory outside runtime bounds")
        keys = {(row.entity_id, row.month) for row in rows}
        if len(keys) != len(rows):
            raise ValueError("Duplicate entity-month in snapshot")
        per_month = defaultdict(list)
        per_entity = defaultdict(list)
        for row in rows:
            per_month[row.month].append(row)
            per_entity[row.entity_id].append(row)
        for group in per_month.values():
            group.sort(key=lambda r: (r.probability is None, -(r.probability or 0), r.entity_id))
        for history in per_entity.values():
            history.sort(key=lambda r: r.month)
        return report, manifest, per_month, per_entity


def create_app(root: Path | None = None, frontend: Path | None = None) -> FastAPI:
    source = Snapshot(root or Path(os.environ.get("RADAR_ARTIFACTS", "artifacts")))
    app = FastAPI(title="Carteira Radar", version="1.0", redoc_url=None)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path not in {"/docs", "/docs/oauth2-redirect"}:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )
        if request.url.path.startswith(("/api/", "/health/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def current():
        try:
            return source.contents
        except (OSError, TypeError, ValueError, ValidationError, UnicodeError) as exc:
            raise HTTPException(503, "Snapshot indisponível ou inválido.") from exc

    @app.get("/health/ready")
    def ready():
        _, manifest, _, _ = current()
        return {"status": "ready", "run_id": manifest["run_id"]}

    @app.get("/api/v1/benchmark")
    def benchmark():
        report, manifest, _, _ = current()
        return {**report, "provenance": manifest}

    @app.get("/api/v1/predictions", response_model=PredictionPage)
    def predictions(
        month: Annotated[str | None, Query(pattern=MONTH)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=250_000)] = 0,
    ):
        _, _, by_month, _ = current()
        months = sorted(by_month, reverse=True)
        selected = month or months[0]
        if selected not in by_month:
            raise HTTPException(404, "Competência não disponível.")
        entries = by_month[selected]
        return PredictionPage(
            month=selected,
            months=months,
            total=len(entries),
            scored=sum(row.status == "scored" for row in entries),
            offset=offset,
            predictions=entries[offset : offset + limit],
        )

    @app.get("/api/v1/entities/{entity_id:path}", response_model=EntityHistory)
    def entity(entity_id: str):
        if not re.fullmatch(ENTITY, entity_id):
            raise HTTPException(404, "Carteira não encontrada.")
        _, _, _, by_entity = current()
        if entity_id not in by_entity:
            raise HTTPException(404, "Carteira não encontrada.")
        return EntityHistory(entity_id=entity_id, history=by_entity[entity_id])

    frontend = frontend or Path(
        os.environ.get("RADAR_FRONTEND", "frontend/dist/carteira-radar/browser")
    )
    if (frontend / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="dashboard")
    else:

        @app.get("/", include_in_schema=False)
        def no_dashboard():
            raise HTTPException(503, "Painel não compilado. Consulte a documentação do projeto.")

    return app


app = create_app()
