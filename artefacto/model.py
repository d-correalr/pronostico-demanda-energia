from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "processed"
EVIDENCE = ROOT / "data" / "evidence"
ARTIFACTS = ROOT / ".artifacts"
SEED = 42
PARAMS = {"n_estimators": 250, "max_depth": 8, "min_samples_leaf": 12, "max_features": 0.8}
FEATURES = [
    "lag_1", "lag_7", "lag_14", "lag_28", "media_7", "desv_7", "media_28", "desv_28",
    "cambio_7", "dow_sin", "dow_cos", "doy_sin", "doy_cos", "es_fin_semana", "es_festivo", "tendencia",
]
FEATURE_NAMES = [
    "Demanda del día anterior", "Demanda de hace 7 días", "Demanda de hace 14 días", "Demanda de hace 28 días",
    "Media de 7 días", "Variación de 7 días", "Media de 28 días", "Variación de 28 días", "Cambio semanal",
    "Ciclo semanal (seno)", "Ciclo semanal (coseno)", "Ciclo anual (seno)", "Ciclo anual (coseno)",
    "Fin de semana", "Festivo", "Tendencia",
]
HOLIDAYS = pd.to_datetime([
    "2025-01-01", "2025-01-06", "2025-03-24", "2025-04-17", "2025-04-18", "2025-05-01",
    "2025-06-02", "2025-06-23", "2025-06-30", "2025-07-20", "2025-08-07", "2025-08-18",
    "2025-10-13", "2025-11-03", "2025-11-17", "2025-12-08", "2025-12-25",
    "2026-01-01", "2026-01-12", "2026-03-23", "2026-04-02", "2026-04-03", "2026-05-01",
    "2026-05-18", "2026-06-08", "2026-06-15", "2026-06-29", "2026-07-20", "2026-08-07",
    "2026-08-17", "2026-10-12", "2026-11-02", "2026-11-16", "2026-12-08", "2026-12-25",
])
MODEL_NAMES = {
    "extra_trees": "Extra Trees", "estacional_7": "Estacional de 7 días",
    "media_movil_7": "Media móvil de 7 días", "ultimo_valor": "Último valor",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matches_source_data(path: Path, expected_sha256: str) -> bool:
    data = path.read_bytes()
    lf = data.replace(b"\r\n", b"\n")
    variants = (data, lf, lf.replace(b"\n", b"\r\n"))
    return any(hashlib.sha256(value).hexdigest() == expected_sha256 for value in variants)


def load_daily(path: Path = DATA / "base_modelo_diaria.csv") -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"fecha", "demanda_total_kwh", "consumo_facturado_calendarizado_total_kwh"}
    if not required.issubset(frame.columns):
        raise ValueError("La base diaria no contiene las columnas obligatorias.")
    forbidden = {"niu", "direccion", "id_factura", "codigo_medidor", "latitud", "longitud", "predial"}
    if forbidden.intersection(str(col).lower() for col in frame.columns):
        raise ValueError("La base contiene identificadores no permitidos.")
    frame["fecha"] = pd.to_datetime(frame["fecha"], errors="raise")
    frame = frame.sort_values("fecha").set_index("fecha")
    if len(frame) < 365 or not frame.index.is_unique:
        raise ValueError("Se requiere al menos un año de fechas únicas.")
    expected = pd.date_range(frame.index.min(), frame.index.max())
    if not frame.index.equals(expected):
        raise ValueError("La serie diaria debe contener fechas continuas y ordenadas.")
    values = frame["demanda_total_kwh"].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("La demanda debe ser numérica, finita y no negativa.")
    return frame


def feature_row(history: pd.Series, date: pd.Timestamp) -> dict:
    if len(history) < 28 or history.index.max() >= date:
        raise ValueError("Los rezagos requieren 28 días anteriores al pronóstico.")
    values = history.to_numpy(dtype=float)
    dow, doy = date.dayofweek, date.dayofyear
    return {
        "lag_1": values[-1], "lag_7": values[-7], "lag_14": values[-14], "lag_28": values[-28],
        "media_7": values[-7:].mean(), "desv_7": values[-7:].std(ddof=0),
        "media_28": values[-28:].mean(), "desv_28": values[-28:].std(ddof=0),
        "cambio_7": values[-1] - values[-7], "dow_sin": np.sin(2*np.pi*dow/7),
        "dow_cos": np.cos(2*np.pi*dow/7), "doy_sin": np.sin(2*np.pi*doy/365.25),
        "doy_cos": np.cos(2*np.pi*doy/365.25), "es_fin_semana": float(dow >= 5),
        "es_festivo": float(date.normalize() in HOLIDAYS),
        "tendencia": float((date-pd.Timestamp("2025-01-01")).days/365.25),
    }


def supervised(series: pd.Series):
    rows = [feature_row(series.iloc[:i], date) for i, date in enumerate(series.index) if i >= 28]
    return pd.DataFrame(rows, index=series.index[28:])[FEATURES], series.iloc[28:]


def fit_model(history: pd.Series):
    model = ExtraTreesRegressor(random_state=SEED, n_jobs=1, **PARAMS)
    x, y = supervised(history)
    model.fit(x, y)
    return model


def predict_recursive(model, history: pd.Series, dates: pd.DatetimeIndex):
    simulated = history.copy().astype(float)
    predictions, rows = [], []
    for date in dates:
        row = feature_row(simulated, date)
        prediction = max(0.0, float(model.predict(pd.DataFrame([row], columns=FEATURES))[0]))
        simulated.loc[date] = prediction
        predictions.append(prediction)
        rows.append(row)
    return pd.Series(predictions, index=dates), pd.DataFrame(rows, index=dates)[FEATURES]


def predict_baseline(name, history, dates):
    if name not in MODEL_NAMES or name == "extra_trees":
        raise ValueError("Baseline no permitido.")
    simulated = history.copy().astype(float)
    result = []
    for date in dates:
        if name == "estacional_7":
            value = float(simulated.iloc[-7])
        elif name == "media_movil_7":
            value = float(simulated.iloc[-7:].mean())
        else:
            value = float(simulated.iloc[-1])
        result.append(max(0.0, value))
        simulated.loc[date] = value
    return pd.Series(result, index=dates)


def metrics(actual, predicted):
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if actual.shape != predicted.shape or not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ValueError("Los valores observados y pronosticados deben coincidir y ser finitos.")
    denominator = float(np.abs(actual).sum())
    if denominator <= 0:
        raise ValueError("La demanda acumulada debe ser positiva.")
    error = predicted-actual
    return {
        "wape": float(np.abs(error).sum()/denominator), "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(mean_squared_error(actual, predicted)**0.5), "sesgo_pct": float(error.sum()/denominator),
        "r2": float(r2_score(actual, predicted)),
    }


def explain_path(model, row: pd.DataFrame):
    contributions = np.zeros(len(FEATURES))
    base = 0.0
    values = row.iloc[0].to_numpy(dtype=np.float32)
    for estimator in model.estimators_:
        tree = estimator.tree_
        node = 0
        base += float(tree.value[0, 0, 0])
        while tree.children_left[node] != -1:
            feature = tree.feature[node]
            child = tree.children_left[node] if values[feature] <= tree.threshold[node] else tree.children_right[node]
            contributions[feature] += tree.value[child, 0, 0]-tree.value[node, 0, 0]
            node = child
    base /= len(model.estimators_)
    contributions /= len(model.estimators_)
    prediction = float(model.predict(row)[0])
    if not np.isclose(base+contributions.sum(), prediction, atol=1e-6, rtol=0):
        raise ValueError("La explicación local no concilia con la predicción.")
    return {
        "base_kwh": base, "prediction_kwh": prediction,
        "contributions": sorted([
            {"feature": feature, "label": label, "kwh": float(contribution)}
            for feature, label, contribution in zip(FEATURES, FEATURE_NAMES, contributions)
        ], key=lambda item: abs(item["kwh"]), reverse=True),
        "method": "Descomposición del recorrido de los árboles (no SHAP). Aditiva, dependiente del orden de cortes; no causal.",
    }
