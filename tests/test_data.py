import csv
import hashlib
import io
from zipfile import ZipFile

import pandas as pd
import pytest

from carteira_radar.data import build_dataset


def archive(raw, values, *, partial=False):
    filename = raw / "inf_mensal_fidc_2024.zip"
    with ZipFile(filename, "w") as z:
        for month, amount in values.items():
            for t in ("V", "VI"):
                if partial and t == "VI":
                    continue
                stream = io.StringIO()
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(
                    [
                        "TP_FUNDO_CLASSE",
                        "CNPJ_FUNDO_CLASSE",
                        "DENOM_SOCIAL",
                        "DT_COMPTC",
                        f"TAB_{t}_A_VL_DIRCRED_PRAZO",
                        f"TAB_{t}_B_VL_DIRCRED_INAD",
                        *[
                            f"TAB_{t}_B{i}_VL_INAD_{n}"
                            for i, n in enumerate(
                                [30, 60, 90, 120, 150, 180, 360, 720, 1080, "MAIOR_1080"], 1
                            )
                        ],
                    ]
                )
                for kind, balance, overdue, bucket in amount:
                    writer.writerow(
                        [
                            kind,
                            "06.081.379/0001-98",
                            "FIDC TESTE",
                            f"2024-{month}-28",
                            balance if t == "V" else 0,
                            overdue if t == "V" else 0,
                            0,
                            0,
                            0,
                            bucket if t == "V" else 0,
                            *([0] * 6),
                        ]
                    )
                z.writestr(f"inf_mensal_fidc_tab_{t}_2024{month}.csv", stream.getvalue())
    return filename


def test_exact_calendar_cutoff_and_threshold(tmp_path, monkeypatch):
    def absent(raw, name, url):
        path = raw / name
        return path if path.exists() else None

    monkeypatch.setattr("carteira_radar.data._fetch", absent)

    def rows(value):
        return [("Classe", str(100 - value), value, value), ("Fundo", 99, 1, 1)]

    path = archive(tmp_path, {"01": rows(10), "03": rows(85), "04": rows(12), "05": rows(99)})
    df, meta = build_dataset(tmp_path, "2024-01", "2024-04")
    classes = df[df.entity_id.str.startswith("Classe:")].set_index("month")
    jan, mar, apr = (classes.loc[f"2024-{m}-01"] for m in ("01", "03", "04"))
    assert jan.label == 1
    assert jan.outcome_ratio == 0.12
    assert pd.isna(jan.lag_1) and pd.isna(jan.lag_3)
    assert pd.isna(mar.lag_1) and pd.isna(mar.label)
    assert apr.lag_1 == 0.85 and apr.lag_3 == 0.1 and apr.delta_3 == pytest.approx(0.02)
    assert apr.label_month == pd.Timestamp("2024-07-01") and pd.isna(apr.label)
    assert len(df) == 6 and str(df.label.dtype) == "Int8"
    assert meta["missing_months"] == ["2024-02"]
    assert meta["archives"][path.name]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_duplicate_fails_even_if_first_filing_has_invalid_amount(tmp_path):
    archive(tmp_path, {"01": [("Classe", 90, 10, ""), ("Classe", 90, 10, 10)]})
    with pytest.raises(ValueError, match="duplicate entity-month"):
        build_dataset(tmp_path, "2024-01", "2024-01")


def test_invalid_values_and_incoherent_overdue_are_not_zero_filled(tmp_path):
    archive(
        tmp_path, {"01": [("Classe", 90, 10, ""), ("Fundo", 90, 10, 9), ("Pessoa", 90, 10, 10)]}
    )
    df, meta = build_dataset(tmp_path, "2024-01", "2024-01")
    assert df.empty
    errors = meta["coverage"]["2024-01"]["rejections"]
    assert (
        errors["invalid_amount_V"] == errors["incoherent_overdue_V"] == errors["invalid_id_V"] == 1
    )


def test_identical_filings_count_once(tmp_path):
    row = ("Classe", 90, 10, 10)
    archive(tmp_path, {"01": [row, row]})
    df, meta = build_dataset(tmp_path, "2024-01", "2024-01")
    assert len(df) == 1
    rejections = meta["coverage"]["2024-01"]["rejections"]
    assert rejections["identical_duplicate_V"] == rejections["identical_duplicate_VI"] == 1


def test_annual_partial_tables_fail_without_monthly_replacement(tmp_path, monkeypatch):
    path = archive(tmp_path, {"01": [("Classe", 90, 10, 10)]}, partial=True)
    calls = []

    def fetch(raw, name, url):
        calls.append(url)
        return path if name == path.name else None

    monkeypatch.setattr("carteira_radar.data._fetch", fetch)
    with pytest.raises(ValueError, match="required V/VI members incomplete"):
        build_dataset(tmp_path, "2024-01", "2024-01")
    assert calls[0].endswith("/DADOS/HIST/inf_mensal_fidc_2024.zip")
    assert calls[1].endswith("/DADOS/inf_mensal_fidc_202401.zip")
