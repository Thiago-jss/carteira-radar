"""Acquire public CVM FIDC filings and build exact-calendar model observations."""

import csv
import hashlib
import io
import math
import os
import re
import tempfile
import time
from collections import Counter
from contextlib import ExitStack
from datetime import date
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, build_opener
from zipfile import ZipFile

import pandas as pd

_BASE = "https://dados.cvm.gov.br/dados/FIDC/DOC/INF_MENSAL/DADOS/"
_ARCHIVE_LIMIT = 256 * 1024**2
_CSV_LIMIT = 32 * 1024**2
_ROW_LIMIT = 100_000
_KEY = ("TP_FUNDO_CLASSE", "CNPJ_FUNDO_CLASSE", "DENOM_SOCIAL", "DT_COMPTC")
_BUCKETS = (30, 60, 90, 120, 150, 180, 360, 720, 1080, "MAIOR_1080")
_COLUMNS = (
    "entity_id",
    "name",
    "month",
    "ratio",
    "lag_1",
    "lag_3",
    "delta_3",
    "log_balance",
    "label",
    "outcome_ratio",
    "label_month",
    "outcome_reason",
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch(raw, name, url):
    path = raw / name
    if path.is_file():
        return path
    deadline = time.monotonic() + 180
    temporary = None
    try:
        try:
            response = build_opener(_NoRedirect()).open(url, timeout=30)
        except HTTPError as exc:
            if exc.code == 404:
                exc.close()
                return None
            exc.close()
            raise
        with response, tempfile.NamedTemporaryFile(dir=raw, prefix=".cvm-", delete=False) as out:
            temporary = Path(out.name)
            size = 0
            length = response.headers.get("Content-Length")
            if length is not None and not 0 < int(length) <= _ARCHIVE_LIMIT:
                raise ValueError(f"{name}: invalid archive Content-Length")
            while chunk := response.read1(1024**2):
                size += len(chunk)
                if size > _ARCHIVE_LIMIT or time.monotonic() > deadline:
                    raise ValueError(f"{name}: download byte/time limit exceeded")
                out.write(chunk)
            if length is not None and size != int(length):
                raise ValueError(f"{name}: incomplete download")
        with ZipFile(temporary) as archive:
            if not archive.namelist():
                raise ValueError(f"{name}: empty archive")
        os.replace(temporary, path)
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _open_archive(stack, path, url, metadata):
    stream = stack.enter_context(path.open("rb"))
    if not 0 < os.fstat(stream.fileno()).st_size <= _ARCHIVE_LIMIT:
        raise ValueError(f"{path.name}: archive size out of bounds")
    sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    archive = stack.enter_context(ZipFile(stream))
    metadata[path.name] = {"url": url, "sha256": sha256, "bytes": os.fstat(stream.fileno()).st_size}
    return archive


def _members(archive, month):
    expected = {
        f"inf_mensal_fidc_tab_{tab}_{month.strftime('%Y%m')}.csv": tab for tab in ("V", "VI")
    }
    result = {}
    if archive is not None:
        for info in archive.infolist():
            if info.filename in expected:
                tab = expected[info.filename]
                if tab in result:
                    raise ValueError(f"{month}: duplicate ZIP member {info.filename}")
                result[tab] = info
    return result


def _amount(token):
    # Fixed decimal form bounds Decimal precision and excludes NaN/Infinity/exponents.
    token = token.strip()
    if not re.fullmatch(r"[0-9]{1,18}(?:[.,][0-9]{1,6})?", token):
        raise ValueError("invalid amount")
    return Decimal(token.replace(",", "."))


def _entity(kind, cnpj):
    if kind not in ("Fundo", "Classe") or not re.fullmatch(
        r"(?:[0-9]{14}|[0-9]{2}\.[0-9]{3}\.[0-9]{3}/[0-9]{4}-[0-9]{2})", cnpj
    ):
        return None
    digits = re.sub(r"[^0-9]", "", cnpj)
    if len(set(digits)) == 1:
        return None
    for offset, weights in (
        (12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
        (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
    ):
        n = 11 - sum(int(digit) * w for digit, w in zip(digits, weights)) % 11
        if int(digits[offset]) != (n if n < 10 else 0):
            return None
    return f"{kind}:{digits}"


def _table(archive, info, tab, month, counts):
    if not 0 < info.file_size <= _CSV_LIMIT:
        raise ValueError(f"{info.filename}: CSV size out of bounds")
    with archive.open(info) as file:
        payload = file.read(_CSV_LIMIT + 1)
    if len(payload) != info.file_size:
        raise ValueError(f"{info.filename}: inconsistent CSV size")
    encoding = "utf-8-sig" if payload.startswith(b"\xef\xbb\xbf") else "latin-1"
    reader = csv.reader(
        io.StringIO(payload.decode(encoding), newline=""), delimiter=";", strict=True
    )
    cols = (
        f"TAB_{tab}_A_VL_DIRCRED_PRAZO",
        f"TAB_{tab}_B_VL_DIRCRED_INAD",
        *(f"TAB_{tab}_B{i}_VL_INAD_{v}" for i, v in enumerate(_BUCKETS, 1)),
    )
    header = next(reader, [])
    if len(header) != len(set(header)) or not set(_KEY + cols) <= set(header):
        raise ValueError(f"{info.filename}: unexpected CSV schema")
    indexes = [header.index(col) for col in _KEY + cols]
    rows = {}
    source_rows = {}
    for n, row in enumerate(reader, 1):
        if n > _ROW_LIMIT or (row and len(row) != len(header)):
            raise ValueError(f"{info.filename}: CSV row limit/shape violation")
        if not row:
            continue
        kind, cnpj, name, competency, *amounts = (row[i].strip() for i in indexes)
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", competency) or date.fromisoformat(
            competency
        ).strftime("%Y-%m") != str(month):
            raise ValueError(f"{info.filename}: row outside competence month or invalid date")
        entity_id = _entity(kind, cnpj)
        if entity_id is None:
            counts[f"invalid_id_{tab}"] += 1
            continue
        if entity_id in source_rows:
            if row != source_rows[entity_id]:
                raise ValueError(
                    f"{info.filename}: duplicate entity-month key with conflicting values"
                )
            counts[f"identical_duplicate_{tab}"] += 1
            continue
        source_rows[entity_id] = row
        rows[entity_id] = None  # Reserve the key even if the filing is invalid.
        try:
            a, b, *buckets = map(_amount, amounts)
        except ValueError:
            counts[f"invalid_amount_{tab}"] += 1
            continue
        if sum(buckets) != b:
            counts[f"incoherent_overdue_{tab}"] += 1
            continue
        rows[entity_id] = name, sum(buckets[3:]), a + b
    return rows


def _observations(archive, members, month, counts):
    v, vi = (_table(archive, members[t], t, month, counts) for t in ("V", "VI"))
    counts["missing_counterpart"] = len(v.keys() ^ vi.keys())
    for key in sorted(v.keys() & vi.keys()):
        if v[key] is None or vi[key] is None:
            continue
        name, n, d = v[key]
        other_name, other_n, other_d = vi[key]
        if d + other_d <= 0:
            counts["nonpositive_balance"] += 1
            continue
        yield key, name or other_name, Fraction(n + other_n) / Fraction(d + other_d), d + other_d


def _features(observations, end):
    rows = []
    for (month, entity_id), (name, ratio, balance) in sorted(observations.items()):

        def ratio_at(offset, base_month=month, key=entity_id):
            observation = observations.get((base_month + offset, key))
            return observation[1] if observation is not None else None

        lag1, lag3, future = map(ratio_at, (-1, -3, 3))
        reason = ("beyond_end" if month + 3 > end else "unavailable") if future is None else None
        rows.append(
            (
                entity_id,
                name,
                month.start_time,
                float(ratio),
                float(lag1) if lag1 is not None else math.nan,
                float(lag3) if lag3 is not None else math.nan,
                float(ratio - lag3) if lag3 is not None else math.nan,
                math.log(float(balance)),
                int(future - ratio >= Fraction(1, 50)) if future is not None else None,
                float(future) if future is not None else math.nan,
                (month + 3).start_time,
                reason,
            )
        )
    return pd.DataFrame(rows, columns=_COLUMNS).astype(
        {
            **{
                key: "float64"
                for key in ("ratio", "lag_1", "lag_3", "delta_3", "log_balance", "outcome_ratio")
            },
            "label": "Int8",
            "month": "datetime64[ns]",
            "label_month": "datetime64[ns]",
            "entity_id": "string",
            "name": "string",
            "outcome_reason": "string",
        }
    )


def build_dataset(raw: Path, start: str, end: str) -> tuple[pd.DataFrame, dict]:
    """Build the inclusive competence window; never use data outside it for features or labels."""
    if (
        any(
            not isinstance(s, str) or not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", s)
            for s in (start, end)
        )
        or not "1678-01" <= start <= end <= "2261-12"
    ):
        raise ValueError("expected 1678-01 <= start <= end <= 2261-12 (YYYY-MM)")
    periods = pd.period_range(start, end, freq="M")
    raw = Path(raw)
    raw.mkdir(parents=True, exist_ok=True)
    observations, archives, coverage = {}, {}, {}
    for year in range(periods[0].year, periods[-1].year + 1):
        with ExitStack() as annual_stack:
            annual = annual_path = None
            if 2022 <= year <= 2024:
                name = f"inf_mensal_fidc_{year}.zip"
                url = f"{_BASE}HIST/{name}"
                annual_path = _fetch(raw, name, url)
                if annual_path is not None:
                    annual = _open_archive(annual_stack, annual_path, url, archives)
            for month in periods[periods.year == year]:
                counts = Counter()
                with ExitStack() as monthly_stack:
                    archive, path = annual, annual_path
                    members = _members(archive, month)
                    if len(members) != 2:
                        name = f"inf_mensal_fidc_{month.strftime('%Y%m')}.zip"
                        url = _BASE + name
                        alternative = _fetch(raw, name, url)
                        if alternative is not None:
                            path = alternative
                            archive = _open_archive(monthly_stack, path, url, archives)
                            members = _members(archive, month)
                        elif not members:
                            coverage[str(month)] = {"status": "missing", "rows": 0, "archive": None}
                            continue
                    if len(members) != 2:
                        raise ValueError(f"{month}: required V/VI members incomplete in {path}")
                    assert archive is not None and path is not None
                    with localcontext(Context(prec=28)):
                        for entity_id, name, ratio, balance in _observations(
                            archive, members, month, counts
                        ):
                            observations[month, entity_id] = (name, ratio, balance)
                            counts["accepted"] += 1
                    coverage[str(month)] = {
                        "status": "loaded",
                        "archive": path.name,
                        "rows": counts.pop("accepted", 0),
                        "rejections": dict(counts),
                    }
    return _features(observations, periods[-1]), {
        "start": start,
        "end": end,
        "archives": archives,
        "coverage": coverage,
        "missing_months": [m for m, info in coverage.items() if info["status"] == "missing"],
        "point_in_time": False,
        "disclaimer": "Latest acquired CVM archives may include retroactive revisions; not point-in-time.",
    }
