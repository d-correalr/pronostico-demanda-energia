from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


def locate_project_root() -> Path:
    configured = os.getenv("PROYECTO_GRADO_DIR")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path("/content/drive/MyDrive/Proyecto de Grado"))
    location = Path(globals().get("__file__", Path.cwd())).resolve()
    candidates.extend(location.parents)
    candidates.append(Path.cwd())
    for candidate in candidates:
        if (candidate / "Datos").exists():
            return candidate
    raise FileNotFoundError("No se encontró la carpeta Datos")


ROOT = locate_project_root()
SOURCE_DIR = ROOT / "Datos"
DELIVERY_DIR = ROOT / "entregables" / "Modulo_2_modelos_v3"
OUTPUT_DIR = DELIVERY_DIR / "datos_modelo"
ANALYSIS_START = pd.Timestamp("2025-01-01")
ANALYSIS_END = pd.Timestamp("2025-12-31")
CLOSING_PERIOD = pd.Timestamp("2026-01-01")
N_DAYS = (ANALYSIS_END - ANALYSIS_START).days + 1
CHUNK_SIZE = 120_000


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^0-9a-zA-Z]+", "_", normalized).strip("_").lower()


def period_from_path(path: Path) -> pd.Timestamp:
    stem = normalize_name(path.stem)
    if "tc1" in stem:
        match = re.search(r"tc1_(\d{1,2})_(\d{4})", stem)
        if match:
            month, year = int(match.group(1)), int(match.group(2))
            return pd.Timestamp(year=year, month=month, day=1)
        if re.search(r"tc1_ene_?26", stem):
            return pd.Timestamp("2026-01-01")
    if "tc2" in stem:
        match = re.search(r"tc2_(\d{4})(\d{1,2})$", stem)
        if match:
            year, month = int(match.group(1)), int(match.group(2))
            return pd.Timestamp(year=year, month=month, day=1)
    raise ValueError(f"No se pudo extraer el periodo de {path.name}")


def detect_encoding(path: Path) -> str:
    for encoding in ["utf-8-sig", "cp1252", "latin1"]:
        try:
            pd.read_csv(path, nrows=0, encoding=encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"No se pudo detectar la codificación de {path.name}")


def get_header_map(path: Path) -> tuple[dict[str, str], str]:
    encoding = detect_encoding(path)
    columns = pd.read_csv(path, nrows=0, encoding=encoding).columns
    return {normalize_name(column): column for column in columns}, encoding


def get_source_inventory() -> list[dict]:
    rows = []
    for path in sorted(SOURCE_DIR.rglob("*")):
        if path.is_file():
            stat = path.stat()
            rows.append(
                {
                    "relative_path": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "size_bytes": stat.st_size,
                    "modified_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
    return rows


def load_relevant_dictionary() -> pd.DataFrame:
    path = SOURCE_DIR / "Variables TC1 Y TC2.xlsx"
    frame = pd.read_excel(path, sheet_name="Variables relevantes ", header=1)
    frame = frame.rename(columns=lambda value: normalize_name(value))
    frame = frame.loc[:, [column for column in frame if not column.startswith("unnamed")]]
    frame = frame.rename(
        columns={
            "descripcion": "descripcion",
            "numero_de_registros": "numero_registros_referencia",
            "tipo_de_dato": "tipo_dato_referencia",
        }
    )
    frame = frame[frame["campo"].notna()].copy()
    frame["campo_normalizado"] = frame["campo"].map(normalize_name)
    frame["formato"] = frame["formato"].astype(str).str.strip()
    ordered = [
        "formato",
        "campo",
        "campo_normalizado",
        "descripcion",
        "tipo_dato_referencia",
        "numero_registros_referencia",
    ]
    return frame[ordered].reset_index(drop=True)


def catalog_source_files() -> tuple[dict[pd.Timestamp, Path], dict[pd.Timestamp, Path]]:
    tc1_files = {}
    tc2_files = {}
    for path in (SOURCE_DIR / "TC1-2025").glob("*.csv"):
        period = period_from_path(path)
        if period in tc1_files:
            raise ValueError(f"TC1 duplicado para {period:%Y-%m}")
        tc1_files[period] = path
    for path in (SOURCE_DIR / "TC2-2025").glob("*.csv"):
        period = period_from_path(path)
        if period in tc2_files:
            raise ValueError(f"TC2 duplicado para {period:%Y-%m}")
        tc2_files[period] = path
    expected = set(pd.date_range(ANALYSIS_START, CLOSING_PERIOD, freq="MS"))
    if set(tc1_files) != expected or set(tc2_files) != expected:
        missing_tc1 = sorted(expected - set(tc1_files))
        missing_tc2 = sorted(expected - set(tc2_files))
        raise ValueError(f"Periodos faltantes TC1={missing_tc1}, TC2={missing_tc2}")
    return tc1_files, tc2_files


def load_tc1_mapping(
    path: Path,
    relevant_fields: set[str],
) -> tuple[pd.DataFrame, dict]:
    header_map, encoding = get_header_map(path)
    aliases = {"rupo_calidad": "grupo_calidad"}
    requested = {
        "niu",
        "id_mercado",
        "nivel_de_tension",
        "conexion_de_red",
        "codigo_dane",
        "ubicacion",
        "condiciones_especiales",
        "estrato_sector",
        "autogenerador",
        "exportar_energia",
        "grupo_calidad",
        "rupo_calidad",
    }
    usecols = [header_map[column] for column in sorted(requested) if column in header_map]
    frame = pd.read_csv(path, usecols=usecols, dtype=str, encoding=encoding, low_memory=False)
    frame.columns = [aliases.get(normalize_name(column), normalize_name(column)) for column in frame]
    raw_rows = len(frame)
    frame["niu"] = frame["niu"].fillna("").str.strip()
    missing_niu = int(frame["niu"].eq("").sum())
    frame = frame[frame["niu"].ne("")].copy()
    duplicate_rows = int(frame.duplicated("niu", keep="first").sum())
    frame = frame.drop_duplicates("niu", keep="first")
    output_columns = [
        "id_mercado",
        "nivel_de_tension",
        "conexion_de_red",
        "codigo_dane",
        "ubicacion",
        "condiciones_especiales",
        "estrato_sector",
        "autogenerador",
        "exportar_energia",
        "grupo_calidad",
    ]
    for column in output_columns:
        if column not in frame:
            frame[column] = "SIN_DATO"
        frame[column] = frame[column].fillna("SIN_DATO").astype(str).str.strip()
        frame.loc[frame[column].eq(""), column] = "SIN_DATO"
    normalized_schema = {aliases.get(normalize_name(column), normalize_name(column)) for column in usecols}
    missing_relevant = sorted(
        field for field in relevant_fields if field not in normalized_schema and field != "niu"
    )
    stats = {
        "rows": int(raw_rows),
        "unique_niu": int(len(frame)),
        "missing_niu_rows": missing_niu,
        "duplicate_niu_rows": duplicate_rows,
        "encoding": encoding,
        "schema_normalized": sorted(normalized_schema),
        "missing_relevant_fields": missing_relevant,
    }
    return frame.set_index("niu"), stats


def cadence_from_days(days: pd.Series) -> pd.Series:
    conditions = [days.between(20, 45), days.between(70, 110)]
    choices = ["mensual", "trimestral"]
    return pd.Series(np.select(conditions, choices, default="irregular"), index=days.index)


def add_grouped_events(
    store: defaultdict[tuple, np.ndarray],
    work: pd.DataFrame,
) -> None:
    group_columns = [
        "indice_fecha",
        "cadencia",
        "id_mercado",
        "ubicacion",
        "estrato_sector",
        "nivel_tension",
    ]
    grouped = (
        work.groupby(group_columns, dropna=False, observed=True)[["delta_energia", "delta_registros"]]
        .sum()
        .reset_index()
    )
    for row in grouped.itertuples(index=False):
        key = (
            int(row.indice_fecha),
            str(row.cadencia),
            str(row.id_mercado),
            str(row.ubicacion),
            str(row.estrato_sector),
            str(row.nivel_tension),
        )
        store[key] += np.array([float(row.delta_energia), float(row.delta_registros)])


def process_tc2_period(
    tc2_path: Path,
    source_period: pd.Timestamp,
    tc1_mapping: pd.DataFrame,
    event_store: defaultdict[tuple, np.ndarray],
    relevant_fields: set[str],
) -> dict:
    header_map, encoding = get_header_map(tc2_path)
    required = {
        "niu",
        "fecha_de_lectura_actual",
        "fecha_de_lectura_anterior",
        "dias_facturados",
        "consumo_usuario_kwh",
    }
    metric_fields = {
        "consumo_promedio_semestral_kwh",
        "valor_facturacion_por_consumo_usuario",
        "refacturacion_por_consumo_usuario_kwh",
        "valor_refacturacion_por_consumo_usuario",
        "valor_del_subsidio_usuario",
        "valor_de_la_contribucion",
        "valor_cartera_consumo",
        "tvc",
        "vc",
        "vcd",
        "vcf",
        "cec_kwh",
        "conpu",
        "thc",
        "hc",
        "valor_total_facturado",
        "tarifa_aplicada_kwh",
        "energia_activa_exportada_kwh",
        "consumo_recuperado_kwh",
        "valor_consumo_recuperado",
    }
    support_fields = {
        "tipo_factura",
        "id_factura",
        "tipo_de_tarifa",
        "tipo_de_lectura",
        "tipo_medidor",
        "id_mercado",
        "ano_de_reporte",
        "mes_de_reporte",
    }
    missing_required = required - set(header_map)
    if missing_required:
        raise ValueError(f"{tc2_path.name}: faltan columnas {sorted(missing_required)}")
    selected = required | metric_fields | support_fields
    usecols = [header_map[column] for column in sorted(selected) if column in header_map]
    selected_normalized = {normalize_name(column) for column in usecols}
    counters = Counter()
    cadence_counter = Counter()
    metric_sums = Counter()
    metric_non_null = Counter()
    seen_hashes = set()
    energy_raw = 0.0
    energy_refact = 0.0
    energy_valid = 0.0
    energy_allocated_2025 = 0.0
    for chunk in pd.read_csv(
        tc2_path,
        usecols=usecols,
        dtype=str,
        chunksize=CHUNK_SIZE,
        encoding=encoding,
        low_memory=False,
    ):
        chunk.columns = [normalize_name(column) for column in chunk]
        counters["rows"] += len(chunk)
        duplicate_key = [
            column
            for column in ["niu", "id_factura", "fecha_de_lectura_actual", "consumo_usuario_kwh"]
            if column in chunk
        ]
        hashes = pd.util.hash_pandas_object(chunk[duplicate_key].fillna(""), index=False).astype("uint64")
        repeated = hashes.duplicated(keep="first") | hashes.isin(seen_hashes)
        counters["duplicate_rows"] += int(repeated.sum())
        seen_hashes.update(hashes.loc[~repeated].tolist())
        chunk = chunk.loc[~repeated].copy()
        for column in metric_fields & set(chunk):
            numeric = pd.to_numeric(chunk[column], errors="coerce")
            metric_sums[column] += float(numeric.fillna(0).sum())
            metric_non_null[column] += int(numeric.notna().sum())
        niu = chunk["niu"].fillna("").str.strip()
        consumption = pd.to_numeric(chunk["consumo_usuario_kwh"], errors="coerce")
        refact = pd.to_numeric(chunk.get("refacturacion_por_consumo_usuario_kwh"), errors="coerce")
        declared_days = pd.to_numeric(chunk["dias_facturados"], errors="coerce")
        previous = pd.to_datetime(chunk["fecha_de_lectura_anterior"], format="%d-%m-%Y", errors="coerce")
        current = pd.to_datetime(chunk["fecha_de_lectura_actual"], format="%d-%m-%Y", errors="coerce")
        observed_days = (current - previous).dt.days
        energy_raw += float(consumption.fillna(0).sum())
        energy_refact += float(refact.fillna(0).sum())
        counters["missing_niu"] += int(niu.eq("").sum())
        counters["missing_consumption"] += int(consumption.isna().sum())
        counters["zero_consumption"] += int(consumption.eq(0).sum())
        counters["negative_consumption"] += int(consumption.lt(0).sum())
        counters["invalid_dates"] += int((previous.isna() | current.isna()).sum())
        counters["days_mismatch"] += int(
            (declared_days.notna() & observed_days.notna() & declared_days.ne(observed_days)).sum()
        )
        if "ano_de_reporte" in chunk:
            report_year = pd.to_numeric(chunk["ano_de_reporte"], errors="coerce")
            counters["report_year_mismatch"] += int(
                (report_year.notna() & report_year.ne(source_period.year)).sum()
            )
        if "mes_de_reporte" in chunk:
            report_month = pd.to_numeric(chunk["mes_de_reporte"], errors="coerce")
            counters["report_month_mismatch"] += int(
                (report_month.notna() & report_month.ne(source_period.month)).sum()
            )
        valid = (
            niu.ne("")
            & consumption.notna()
            & consumption.ge(0)
            & previous.notna()
            & current.notna()
            & observed_days.between(1, 120)
        )
        counters["valid_rows"] += int(valid.sum())
        counters["invalid_rows"] += int((~valid).sum())
        if not valid.any():
            continue
        valid_index = chunk.index[valid]
        niu_valid = niu.loc[valid_index]
        days_valid = observed_days.loc[valid_index].astype(int)
        consumption_valid = consumption.loc[valid_index].astype(float)
        cadence = cadence_from_days(days_valid)
        cadence_counter.update(cadence.tolist())
        energy_valid += float(consumption_valid.sum())
        mapped = tc1_mapping.reindex(niu_valid.to_numpy())
        mapped.index = valid_index
        tc1_match = mapped["ubicacion"].notna()
        counters["tc1_matched_rows"] += int(tc1_match.sum())
        counters["tc1_unmatched_rows"] += int((~tc1_match).sum())
        mapped = mapped.fillna("SIN_TC1")
        if "id_mercado" in chunk:
            market_tc2 = chunk.loc[valid_index, "id_mercado"].fillna("").str.strip()
            market = market_tc2.where(market_tc2.ne(""), mapped["id_mercado"])
            mismatch_market = (
                market_tc2.ne("")
                & mapped["id_mercado"].ne("SIN_TC1")
                & market_tc2.ne(mapped["id_mercado"])
            )
            counters["market_mismatch_rows"] += int(mismatch_market.sum())
        else:
            market = mapped["id_mercado"]
        start = previous.loc[valid_index] + pd.Timedelta(days=1)
        end = current.loc[valid_index]
        overlaps_analysis = end.ge(ANALYSIS_START) & start.le(ANALYSIS_END)
        counters["valid_rows_outside_2025"] += int((~overlaps_analysis).sum())
        if not overlaps_analysis.any():
            continue
        keep_index = valid_index[overlaps_analysis]
        clipped_start = start.loc[keep_index].clip(lower=ANALYSIS_START)
        clipped_end = end.loc[keep_index].clip(upper=ANALYSIS_END)
        allocated_days = (clipped_end - clipped_start).dt.days + 1
        rate = consumption_valid.loc[keep_index] / days_valid.loc[keep_index]
        energy_allocated_2025 += float((rate * allocated_days).sum())
        base = pd.DataFrame(
            {
                "cadencia": cadence.loc[keep_index],
                "id_mercado": market.loc[keep_index].fillna("SIN_DATO"),
                "ubicacion": mapped.loc[keep_index, "ubicacion"],
                "estrato_sector": mapped.loc[keep_index, "estrato_sector"],
                "nivel_tension": mapped.loc[keep_index, "nivel_de_tension"],
                "tasa_diaria": rate,
                "indice_inicio": (clipped_start - ANALYSIS_START).dt.days.astype(int),
                "indice_fin_evento": ((clipped_end - ANALYSIS_START).dt.days + 1).astype(int),
            },
            index=keep_index,
        )
        start_events = base.rename(columns={"indice_inicio": "indice_fecha"}).copy()
        start_events["delta_energia"] = start_events["tasa_diaria"]
        start_events["delta_registros"] = 1.0
        end_events = base.rename(columns={"indice_fin_evento": "indice_fecha"}).copy()
        end_events["delta_energia"] = -end_events["tasa_diaria"]
        end_events["delta_registros"] = -1.0
        event_columns = [
            "indice_fecha",
            "cadencia",
            "id_mercado",
            "ubicacion",
            "estrato_sector",
            "nivel_tension",
            "delta_energia",
            "delta_registros",
        ]
        add_grouped_events(event_store, pd.concat([start_events[event_columns], end_events[event_columns]]))
    metric_means = {
        column: metric_sums[column] / metric_non_null[column]
        for column in metric_sums
        if metric_non_null[column]
    }
    return {
        "file": tc2_path.name,
        "source_period": source_period.strftime("%Y-%m"),
        "encoding": encoding,
        **{key: int(value) for key, value in counters.items()},
        "energy_raw_kwh": energy_raw,
        "energy_refact_kwh": energy_refact,
        "energy_valid_kwh": energy_valid,
        "energy_allocated_2025_kwh": energy_allocated_2025,
        "cadence_rows": dict(cadence_counter),
        "metric_sums": dict(metric_sums),
        "metric_means": metric_means,
        "schema_normalized": sorted(selected_normalized),
        "missing_relevant_fields": sorted(relevant_fields - set(header_map)),
    }


def materialize_segment_daily(event_store: dict[tuple, np.ndarray]) -> pd.DataFrame:
    event_rows = []
    for key, values in event_store.items():
        date_index, cadence, market, location, segment, voltage = key
        event_rows.append(
            {
                "indice_fecha": date_index,
                "cadencia": cadence,
                "id_mercado": market,
                "ubicacion": location,
                "estrato_sector": segment,
                "nivel_tension": voltage,
                "delta_energia": values[0],
                "delta_registros": values[1],
            }
        )
    events = pd.DataFrame(event_rows)
    if events.empty:
        raise ValueError("No se generaron eventos de consumo")
    dimensions = ["cadencia", "id_mercado", "ubicacion", "estrato_sector", "nivel_tension"]
    groups = events[dimensions].drop_duplicates().reset_index(drop=True)
    calendar = pd.DataFrame({"indice_fecha": np.arange(N_DAYS)})
    groups["union"] = 1
    calendar["union"] = 1
    full = groups.merge(calendar, on="union").drop(columns="union")
    in_range_events = events[events["indice_fecha"].between(0, N_DAYS - 1)]
    full = full.merge(in_range_events, on=dimensions + ["indice_fecha"], how="left")
    full[["delta_energia", "delta_registros"]] = full[["delta_energia", "delta_registros"]].fillna(0.0)
    full = full.sort_values(dimensions + ["indice_fecha"])
    full["energia_calendarizada_kwh"] = full.groupby(dimensions, observed=True)["delta_energia"].cumsum()
    full["registros_activos"] = full.groupby(dimensions, observed=True)["delta_registros"].cumsum()
    full["fecha"] = ANALYSIS_START + pd.to_timedelta(full["indice_fecha"], unit="D")
    result = full[["fecha", *dimensions, "energia_calendarizada_kwh", "registros_activos"]].copy()
    result["registros_activos"] = result["registros_activos"].round().astype(int)
    tiny_negative = result["energia_calendarizada_kwh"].between(-1e-6, 0)
    result.loc[tiny_negative, "energia_calendarizada_kwh"] = 0.0
    return result[
        result["energia_calendarizada_kwh"].abs().gt(1e-9) | result["registros_activos"].gt(0)
    ].reset_index(drop=True)


def load_real_daily() -> pd.DataFrame:
    path = SOURCE_DIR / "Demanda_Real_2025.xlsx"
    frame = pd.read_excel(path)
    hour_columns = [column for column in frame if re.fullmatch(r"H\d{2}", str(column))]
    if len(hour_columns) != 24:
        raise ValueError(f"Se esperaban 24 columnas horarias y se encontraron {len(hour_columns)}")
    frame["fecha"] = pd.to_datetime(frame["Fecha"], errors="coerce")
    frame["energia_diaria_kwh"] = frame[hour_columns].apply(pd.to_numeric, errors="coerce").sum(axis=1)
    pivot = frame.pivot_table(index="fecha", columns="Codigo", values="energia_diaria_kwh", aggfunc="sum").reset_index()
    expected_codes = {"DMRE", "DMNR", "PRRE", "PRNR"}
    missing_codes = expected_codes - set(pivot)
    if missing_codes:
        raise ValueError(f"Faltan códigos en demanda real: {sorted(missing_codes)}")
    result = pivot.rename(
        columns={
            "DMRE": "demanda_regulada_kwh",
            "DMNR": "demanda_no_regulada_kwh",
            "PRRE": "perdidas_regulada_kwh",
            "PRNR": "perdidas_no_regulada_kwh",
        }
    )
    result["demanda_total_kwh"] = result["demanda_regulada_kwh"] + result["demanda_no_regulada_kwh"]
    result["perdidas_total_kwh"] = result["perdidas_regulada_kwh"] + result["perdidas_no_regulada_kwh"]
    result = result[result["fecha"].between(ANALYSIS_START, ANALYSIS_END)].copy()
    return result.sort_values("fecha").reset_index(drop=True)


def load_projected_monthly() -> pd.DataFrame:
    path = SOURCE_DIR / "Demanda proyectada 2025.xlsx"
    raw = pd.read_excel(path, header=None)
    dates = pd.to_datetime(raw.iloc[3, 11:23], errors="coerce")
    row_map = {
        "proyectada_subasta_regulada_kwh": 5,
        "proyectada_contratos_regulada_kwh": 6,
        "proyectada_contratos_no_regulada_kwh": 7,
        "proyectada_bolsa_kwh": 8,
        "proyectada_total_kwh": 9,
    }
    result = pd.DataFrame({"periodo": dates.dt.to_period("M").dt.to_timestamp()})
    for column, row_number in row_map.items():
        result[column] = pd.to_numeric(raw.iloc[row_number, 11:23], errors="coerce").to_numpy()
    return result


def build_primary_base(real_daily: pd.DataFrame, segment_daily: pd.DataFrame) -> pd.DataFrame:
    energy = (
        segment_daily.groupby(["fecha", "cadencia"], observed=True)["energia_calendarizada_kwh"]
        .sum()
        .unstack(fill_value=0)
        .rename(columns=lambda value: f"consumo_facturado_{value}_kwh")
        .reset_index()
    )
    active = (
        segment_daily.groupby("fecha", observed=True)["registros_activos"]
        .sum()
        .rename("registros_facturacion_activos")
        .reset_index()
    )
    result = real_daily.merge(energy, on="fecha", how="left").merge(active, on="fecha", how="left")
    cadence_columns = [column for column in result if column.startswith("consumo_facturado_")]
    result[cadence_columns] = result[cadence_columns].fillna(0.0)
    result["registros_facturacion_activos"] = result["registros_facturacion_activos"].fillna(0).astype(int)
    result["consumo_facturado_calendarizado_total_kwh"] = result[cadence_columns].sum(axis=1)
    result["dia_semana"] = result["fecha"].dt.dayofweek
    result["es_fin_de_semana"] = result["dia_semana"].ge(5).astype(int)
    result["mes"] = result["fecha"].dt.month
    result["dia_del_ano"] = result["fecha"].dt.dayofyear
    result["cobertura_calendarizacion_completa"] = 1
    ordered = [
        "fecha",
        "demanda_total_kwh",
        "demanda_regulada_kwh",
        "demanda_no_regulada_kwh",
        "perdidas_total_kwh",
        "perdidas_regulada_kwh",
        "perdidas_no_regulada_kwh",
        "consumo_facturado_calendarizado_total_kwh",
        "consumo_facturado_mensual_kwh",
        "consumo_facturado_trimestral_kwh",
        "consumo_facturado_irregular_kwh",
        "registros_facturacion_activos",
        "dia_semana",
        "es_fin_de_semana",
        "mes",
        "dia_del_ano",
        "cobertura_calendarizacion_completa",
    ]
    for column in ordered:
        if column not in result:
            result[column] = 0.0
    return result[ordered].sort_values("fecha").reset_index(drop=True)


def build_monthly_comparison(primary: pd.DataFrame) -> pd.DataFrame:
    actual = (
        primary.assign(periodo=lambda frame: frame["fecha"].dt.to_period("M").dt.to_timestamp())
        .groupby("periodo", as_index=False)
        .agg(
            demanda_real_total_kwh=("demanda_total_kwh", "sum"),
            demanda_real_regulada_kwh=("demanda_regulada_kwh", "sum"),
            demanda_real_no_regulada_kwh=("demanda_no_regulada_kwh", "sum"),
            consumo_facturado_calendarizado_kwh=("consumo_facturado_calendarizado_total_kwh", "sum"),
            cobertura_calendarizacion_completa=("cobertura_calendarizacion_completa", "min"),
        )
    )
    result = actual.merge(load_projected_monthly(), on="periodo", how="outer").sort_values("periodo")
    result["error_proyeccion_kwh"] = result["proyectada_total_kwh"] - result["demanda_real_total_kwh"]
    result["error_absoluto_proyeccion_pct"] = result["error_proyeccion_kwh"].abs() / result["demanda_real_total_kwh"]
    result["sesgo_proyeccion_pct"] = result["error_proyeccion_kwh"] / result["demanda_real_total_kwh"]
    return result


def build_billing_indicators(monthly_quality: list[dict]) -> pd.DataFrame:
    rows = []
    for item in monthly_quality:
        tc2 = item["tc2"]
        row = {
            "periodo_reporte": item["periodo"],
            "filas_tc1": item["tc1"]["rows"],
            "usuarios_tc1_unicos": item["tc1"]["unique_niu"],
            "filas_tc2": tc2["rows"],
            "filas_tc2_validas": tc2["valid_rows"],
            "filas_tc2_invalidas": tc2["invalid_rows"],
            "filas_tc2_duplicadas": tc2["duplicate_rows"],
            "cruce_tc1_pct": tc2["tc1_matched_rows"] / max(tc2["valid_rows"], 1),
            "consumo_reportado_kwh": tc2["energy_raw_kwh"],
            "consumo_valido_kwh": tc2["energy_valid_kwh"],
            "consumo_asignado_2025_kwh": tc2["energy_allocated_2025_kwh"],
            "refacturacion_kwh": tc2["energy_refact_kwh"],
        }
        for field, value in tc2["metric_sums"].items():
            row[f"suma_{field}"] = value
        for field, value in tc2["metric_means"].items():
            row[f"promedio_{field}"] = value
        rows.append(row)
    return pd.DataFrame(rows).sort_values("periodo_reporte").reset_index(drop=True)


def build_schema_audit(monthly_quality: list[dict]) -> pd.DataFrame:
    rows = []
    for item in monthly_quality:
        for source in ["tc1", "tc2"]:
            stats = item[source]
            rows.append(
                {
                    "periodo": item["periodo"],
                    "fuente": source.upper(),
                    "archivo": stats["file"],
                    "codificacion": stats["encoding"],
                    "campos_leidos": "|".join(stats["schema_normalized"]),
                    "campos_relevantes_ausentes": "|".join(stats["missing_relevant_fields"]),
                }
            )
    return pd.DataFrame(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def privacy_audit(columns: list[str]) -> dict:
    forbidden_tokens = [
        "niu",
        "direccion",
        "predial",
        "cedula_catastral",
        "codigo_medidor",
        "latitud",
        "longitud",
        "id_factura",
    ]
    findings = [column for column in columns if any(token in column for token in forbidden_tokens)]
    if findings:
        raise AssertionError(f"Columnas sensibles detectadas en salida: {findings}")
    return {"forbidden_tokens": forbidden_tokens, "findings": findings, "passed": True}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dictionary = load_relevant_dictionary()
    tc1_relevant = set(dictionary.loc[dictionary["formato"].str.contains("TC1"), "campo_normalizado"])
    tc2_relevant = set(dictionary.loc[dictionary["formato"].str.contains("TC2"), "campo_normalizado"])
    tc1_files, tc2_files = catalog_source_files()
    event_store = defaultdict(lambda: np.zeros(2, dtype=float))
    monthly_quality = []
    for period in sorted(tc1_files):
        tc1_mapping, tc1_stats = load_tc1_mapping(tc1_files[period], tc1_relevant)
        tc2_stats = process_tc2_period(
            tc2_files[period], period, tc1_mapping, event_store, tc2_relevant
        )
        tc1_stats = {"file": tc1_files[period].name, **tc1_stats}
        monthly_quality.append(
            {"periodo": period.strftime("%Y-%m"), "tc1": tc1_stats, "tc2": tc2_stats}
        )
        print(
            f"{period:%Y-%m}: {tc2_stats['valid_rows']:,}/{tc2_stats['rows']:,} válidas; "
            f"cruce TC1 {tc2_stats['tc1_matched_rows'] / max(tc2_stats['valid_rows'], 1):.2%}"
        )
    segment_daily = materialize_segment_daily(event_store)
    real_daily = load_real_daily()
    primary = build_primary_base(real_daily, segment_daily)
    monthly = build_monthly_comparison(primary)
    billing_indicators = build_billing_indicators(monthly_quality)
    schema_audit = build_schema_audit(monthly_quality)
    if len(primary) != N_DAYS or primary["fecha"].nunique() != N_DAYS:
        raise AssertionError("La base primaria no contiene exactamente 365 fechas únicas")
    if primary["demanda_total_kwh"].isna().any():
        raise AssertionError("La variable objetivo contiene nulos")
    if primary["cobertura_calendarizacion_completa"].min() != 1:
        raise AssertionError("La cobertura de calendarización no quedó cerrada")
    privacy = privacy_audit(
        primary.columns.tolist()
        + segment_daily.columns.tolist()
        + billing_indicators.columns.tolist()
    )
    portfolio_total = float(primary["consumo_facturado_calendarizado_total_kwh"].sum())
    segment_total = float(segment_daily["energia_calendarizada_kwh"].sum())
    reconciliation_difference = segment_total - portfolio_total
    if abs(reconciliation_difference) > 1e-4:
        raise AssertionError(f"La agregación segmentada no concilia: {reconciliation_difference}")
    output_paths = {
        "base_modelo_diaria": OUTPUT_DIR / "base_modelo_diaria.csv",
        "comparacion_mensual": OUTPUT_DIR / "comparacion_mensual.csv",
        "consumo_calendarizado_segmento": OUTPUT_DIR / "consumo_calendarizado_segmento.csv",
        "indicadores_facturacion_mensual": OUTPUT_DIR / "indicadores_facturacion_mensual.csv",
        "diccionario_variables_relevantes": OUTPUT_DIR / "diccionario_variables_relevantes.csv",
        "auditoria_esquemas": OUTPUT_DIR / "auditoria_esquemas.csv",
        "reporte_calidad": OUTPUT_DIR / "reporte_calidad.json",
        "manifest": OUTPUT_DIR / "manifest.json",
    }
    primary.to_csv(output_paths["base_modelo_diaria"], index=False, encoding="utf-8-sig", float_format="%.6f")
    monthly.to_csv(output_paths["comparacion_mensual"], index=False, encoding="utf-8-sig", float_format="%.6f")
    segment_daily.to_csv(
        output_paths["consumo_calendarizado_segmento"],
        index=False,
        encoding="utf-8-sig",
        float_format="%.6f",
    )
    billing_indicators.to_csv(
        output_paths["indicadores_facturacion_mensual"],
        index=False,
        encoding="utf-8-sig",
        float_format="%.6f",
    )
    dictionary.to_csv(output_paths["diccionario_variables_relevantes"], index=False, encoding="utf-8-sig")
    schema_audit.to_csv(output_paths["auditoria_esquemas"], index=False, encoding="utf-8-sig")
    quality_report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "analysis_period": [str(ANALYSIS_START.date()), str(ANALYSIS_END.date())],
        "source_periods": [item["periodo"] for item in monthly_quality],
        "dictionary": {
            "file": "Variables TC1 Y TC2.xlsx",
            "relevant_fields": int(len(dictionary)),
            "tc1_fields": int(len(tc1_relevant)),
            "tc2_fields": int(len(tc2_relevant)),
        },
        "calendarization_rule": {
            "interval": "(fecha_lectura_anterior, fecha_lectura_actual]",
            "allocation": "consumo_usuario_kwh / días observados del intervalo",
            "cadence": {
                "mensual": "20 a 45 días",
                "trimestral": "70 a 110 días",
                "irregular": "otros intervalos válidos de 1 a 120 días",
            },
            "deduplication": "NIU, factura, fecha de lectura actual y consumo",
            "refacturation": "Se cuantifica, pero no se asigna al consumo físico del intervalo actual",
            "invalid": "NIU vacío, fecha inválida, intervalo fuera de 1 a 120 días o consumo nulo o negativo",
            "boundary_effect": "TC1 y TC2 de enero de 2026 cierran los ciclos que contienen días de diciembre de 2025",
            "target_scope": "La demanda real y la prueba final permanecen limitadas a 2025",
        },
        "monthly_quality": monthly_quality,
        "output_checks": {
            "primary_rows": int(len(primary)),
            "primary_unique_dates": int(primary["fecha"].nunique()),
            "segment_rows": int(len(segment_daily)),
            "calendarized_portfolio_kwh": portfolio_total,
            "calendarized_segment_kwh": segment_total,
            "reconciliation_difference_kwh": reconciliation_difference,
            "closing_period_included": CLOSING_PERIOD in tc2_files,
            "privacy": privacy,
        },
    }
    output_paths["reporte_calidad"].write_text(
        json.dumps(quality_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_inventory": get_source_inventory(),
        "outputs": [],
    }
    for name, path in output_paths.items():
        if name == "manifest":
            continue
        manifest["outputs"].append(
            {
                "name": name,
                "relative_path": str(path.relative_to(DELIVERY_DIR)).replace("\\", "/"),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    output_paths["manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Archivos listos en {DELIVERY_DIR}")


if __name__ == "__main__":
    main()
