from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from importlib.metadata import version

import joblib
import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance

from . import VERSION
from .model import ARTIFACTS, DATA, EVIDENCE, FEATURES, FEATURE_NAMES, PARAMS, ROOT, SEED
from .model import fit_model, load_daily, matches_source_data, metrics, predict_recursive, sha256, supervised


def prepare():
    daily = load_daily()
    y = daily.demanda_total_kwh
    source_run = json.loads((EVIDENCE / "registro_corrida_guiada.json").read_text(encoding="utf-8"))
    if not matches_source_data(DATA / "base_modelo_diaria.csv", source_run["data_sha256"]):
        raise ValueError("La base cambió frente al Módulo 2. Revalidar antes de empaquetar.")
    split = source_run["split"]
    development_end = pd.Timestamp(split["development_end"])
    calibration_start, calibration_end = map(pd.Timestamp, split["calibration"])
    oot_start, oot_end = map(pd.Timestamp, split["out_of_time"])
    calibration_history = y.loc[:development_end]
    calibration_actual = y.loc[calibration_start:calibration_end]
    calibration_model = fit_model(calibration_history)
    calibration_pred, _ = predict_recursive(calibration_model, calibration_history, calibration_actual.index)
    errors = np.abs(calibration_actual-calibration_pred).to_numpy()
    quantile = min(1.0, np.ceil((len(errors)+1)*0.9)/len(errors))
    q_error = float(np.quantile(errors, quantile, method="higher"))
    final_history = y.loc[:calibration_end]
    validation_model = fit_model(final_history)
    actual = y.loc[oot_start:oot_end]
    prediction, _ = predict_recursive(validation_model, final_history, actual.index)
    golden = pd.read_csv(EVIDENCE / "predicciones_oot.csv")
    if "modelo_extra_trees" not in golden or len(golden) != len(actual):
        raise ValueError("La evidencia no contiene las predicciones OOT de Extra Trees para el periodo vigente.")
    difference = float(np.abs(prediction.to_numpy()-golden.modelo_extra_trees.to_numpy()).max())
    if difference > 1e-6:
        raise ValueError(f"La inferencia no reproduce el notebook: diferencia {difference}.")
    production_history = y.copy()
    model = fit_model(production_history)
    future_start = production_history.index.max() + pd.Timedelta(days=1)
    future_end = future_start + pd.Timedelta(days=91)
    x_train, y_train = supervised(production_history)
    importance = permutation_importance(model, x_train.tail(90), y_train.tail(90),
        scoring="neg_mean_absolute_error", n_repeats=20, random_state=SEED, n_jobs=1)
    ARTIFACTS.mkdir(exist_ok=True)
    model_path = ARTIFACTS / "predictor.joblib"
    validation_model_path = ARTIFACTS / "predictor_validacion.joblib"
    joblib.dump(model, model_path, compress=3)
    joblib.dump(validation_model, validation_model_path, compress=3)
    inputs = list(DATA.glob("*.csv")) + list(DATA.glob("*.json")) + list(EVIDENCE.glob("*"))
    manifest = {
        "artifact_version": VERSION, "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "source_run_id": source_run["run_id"], "seed": SEED, "params": PARAMS,
        "model_family": "extra_trees", "model_label": "Extra Trees",
        "notebook_candidate": source_run["selected"],
        "champion_challenger": source_run["champion_challenger"],
        "split": split,
        "data_sha256": sha256(DATA / "base_modelo_diaria.csv"), "model_sha256": sha256(model_path),
        "validation_model_sha256": sha256(validation_model_path),
        "production_training_end": production_history.index.max().strftime("%Y-%m-%d"),
        "future_period": [future_start.strftime("%Y-%m-%d"), future_end.strftime("%Y-%m-%d")],
        "notebook_data_sha256": source_run["data_sha256"],
        "source_compatibility": "Se admite exclusivamente la conversión LF/CRLF de Git entre sistemas; valores y demás bytes deben coincidir. Los hashes de ejecución corresponden a los archivos exactos del entorno.",
        "input_hashes": {str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path) for path in inputs if path.is_file()},
        "source_hashes": {str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path) for path in (ROOT / "artefacto").rglob("*") if path.is_file() and path.suffix in {".py", ".html", ".css", ".js", ".svg"}},
        "environment": {name: version(name) for name in ["scikit-learn", "numpy", "pandas", "scipy", "fastapi", "plotly", "joblib"]},
        "python": platform.python_version(), "max_prediction_difference_kwh": difference,
        "calibration_metrics": metrics(calibration_actual, calibration_pred),
        "oot_metrics": metrics(actual, prediction), "q_error_kwh": q_error,
        "oot_coverage": float(((actual.to_numpy() >= prediction.to_numpy()-q_error) & (actual.to_numpy() <= prediction.to_numpy()+q_error)).mean()),
        "nominal_coverage": 0.9,
        "importance": [
            {"feature": f, "label": name, "mae_increase_kwh": float(value), "std_kwh": float(std)}
            for f, name, value, std in zip(FEATURES, FEATURE_NAMES, importance.importances_mean, importance.importances_std)
        ],
        "importance_scope": f"Permutación en las últimas 90 observaciones del modelo de producción entrenado hasta {production_history.index.max():%Y-%m-%d}; diagnóstico global, no evaluación independiente.",
    }
    (ARTIFACTS / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(f"Artefacto {VERSION} preparado. Diferencia de validación frente al notebook: {difference:.10f} kWh.")
    return manifest


if __name__ == "__main__":
    prepare()
