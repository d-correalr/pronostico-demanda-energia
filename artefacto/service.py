from __future__ import annotations

import hashlib
import io
import json
import platform
import zipfile
from datetime import datetime, timezone
from importlib.metadata import version

import joblib
import numpy as np
import pandas as pd

from .model import ARTIFACTS, DATA, EVIDENCE, MODEL_NAMES, ROOT
from .model import explain_path, load_daily, metrics, predict_baseline, predict_recursive, sha256


def records(frame):
    return json.loads(frame.to_json(orient="records", date_format="iso"))


class Engine:
    def __init__(self):
        self.manifest = json.loads((ARTIFACTS / "manifest.json").read_text(encoding="utf-8"))
        for relative, digest in {**self.manifest["input_hashes"], **self.manifest["source_hashes"]}.items():
            path = ROOT / relative
            if not path.is_file() or sha256(path) != digest:
                raise ValueError("Cambió un archivo de datos o código. Ejecute python -m artefacto.prepare.")
        if sha256(ARTIFACTS / "predictor.joblib") != self.manifest["model_sha256"]:
            raise ValueError("El modelo no coincide con su manifiesto. No se cargará.")
        if sha256(ARTIFACTS / "predictor_validacion.joblib") != self.manifest["validation_model_sha256"]:
            raise ValueError("El modelo de validación no coincide con su manifiesto. No se cargará.")
        for package in ["scikit-learn", "numpy", "pandas", "scipy", "joblib"]:
            if version(package) != self.manifest["environment"][package]:
                raise ValueError(f"Versión incompatible de {package}; regenere el artefacto en este entorno.")
        self.model = joblib.load(ARTIFACTS / "predictor.joblib")
        self.daily = load_daily()
        calibration_start, calibration_end = self.manifest["split"]["calibration"]
        forecast_start, forecast_end = self.manifest["future_period"]
        self.training_end = pd.Timestamp(self.manifest["production_training_end"])
        self.calibration_start = pd.Timestamp(calibration_start)
        self.calibration_end = pd.Timestamp(calibration_end)
        self.history = self.daily.demanda_total_kwh.copy()
        self.dates = pd.date_range(forecast_start, forecast_end)
        self.prediction, self.feature_rows = predict_recursive(self.model, self.history, self.dates)
        self.baselines = {name: predict_baseline(name, self.history, self.dates) for name in MODEL_NAMES if name != "extra_trees"}
        self.monthly = pd.read_csv(DATA / "comparacion_mensual.csv")
        self.billing = pd.read_csv(DATA / "indicadores_facturacion_mensual.csv")
        self.quality = json.loads((DATA / "reporte_calidad.json").read_text(encoding="utf-8"))
        self.families = pd.read_csv(EVIDENCE / "comparacion_familias.csv")
        self.oot_metrics = pd.read_csv(EVIDENCE / "metricas_oot.csv")

    def scenario(self, horizon=31, method="extra_trees", reference_mwh=None):
        if horizon not in (31, 61, 92) or method not in MODEL_NAMES:
            raise ValueError("Horizonte o método no admitido.")
        if reference_mwh is None:
            reference_mwh = float(self.prediction.iloc[:horizon].sum()/1000)
        if not np.isfinite(reference_mwh) or reference_mwh < 0 or reference_mwh > 1e9:
            raise ValueError("El volumen de referencia debe ser finito y estar entre 0 y 1.000 millones de MWh.")
        dates = self.dates[:horizon]
        predicted = self.prediction.loc[dates] if method == "extra_trees" else self.baselines[method].loc[dates]
        baseline = self.baselines["estacional_7"].loc[dates]
        table = pd.DataFrame({"fecha": dates.strftime("%Y-%m-%d"),
            "prediccion_kwh": predicted.to_numpy(), "baseline_kwh": baseline.to_numpy()})
        q = self.manifest["q_error_kwh"]
        width = None
        if method == "extra_trees":
            table["inferior_kwh"] = np.maximum(0, predicted.to_numpy()-q)
            table["superior_kwh"] = predicted.to_numpy()+q
            width = float((table.superior_kwh-table.inferior_kwh).mean())
        estimated_mwh = float(predicted.sum()/1000)
        diff = reference_mwh-estimated_mwh
        identity = {"method": method, "horizon": horizon, "reference_mwh": float(reference_mwh),
            "model": self.manifest["model_sha256"], "data": self.manifest["data_sha256"]}
        run_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
        peak = table.iloc[table.prediccion_kwh.argmax()]
        validation_metrics = self.manifest["oot_metrics"]
        baseline_validation = self.oot_metrics.loc[self.oot_metrics.alternativa.eq("estacional_7")].iloc[0]
        validation_improvement = 1-validation_metrics["wape"]/float(baseline_validation.wape)
        return {
            "run_id": run_id, "created_utc": datetime.now(timezone.utc).isoformat(),
            "method": method, "method_label": MODEL_NAMES[method], "horizon": horizon,
            "start": dates.min().strftime("%Y-%m-%d"), "end": dates.max().strftime("%Y-%m-%d"),
            "training_end": self.training_end.strftime("%Y-%m-%d"),
            "calibration": f"{self.calibration_start:%Y-%m-%d} / {self.calibration_end:%Y-%m-%d}",
            "forecast_mwh": estimated_mwh, "daily_average_mwh": float(predicted.mean()/1000),
            "reference_mwh": float(reference_mwh), "reference_difference_mwh": diff,
            "reference_difference_pct": diff/reference_mwh if reference_mwh else None,
            "validation_metrics": validation_metrics,
            "baseline_validation_wape": float(baseline_validation.wape),
            "validation_improvement_wape": validation_improvement,
            "interval": {"available": method == "extra_trees", "nominal": 0.9 if method == "extra_trees" else None,
                "validation_coverage": self.manifest["oot_coverage"] if method == "extra_trees" else None,
                "mean_width_kwh": width,
                "label": "Rango diario exploratorio calibrado con mayo y junio; su cobertura futura solo puede medirse cuando llegue la demanda real." if method == "extra_trees" else "Sin intervalo calibrado para este baseline."},
            "peak_day": {"date": peak.fecha, "prediction_kwh": float(peak.prediccion_kwh)},
            "rows": records(table), "manifest": self.manifest,
            "execution_environment": {"python": platform.python_version(), "scikit-learn": version("scikit-learn")},
            "limitations": [f"Pronóstico recursivo agregado emitido con información hasta {self.training_end:%Y-%m-%d}.",
                "Los indicadores de error pertenecen a la validación de julio de 2026, no a los meses futuros.",
                "Los horizontes de dos y tres meses exceden la ventana final de 31 días y deben interpretarse con mayor cautela.",
                "La comparación con una referencia no constituye una orden de compra ni optimización económica.",
                "El clima todavía no participa en la predicción porque falta una serie meteorológica diaria ponderada para el portafolio."],
        }

    def overview(self):
        daily = self.daily.reset_index()
        daily["fecha"] = daily.fecha.dt.strftime("%Y-%m-%d")
        history_cols = ["fecha", "demanda_total_kwh", "consumo_facturado_calendarizado_total_kwh",
            "consumo_facturado_mensual_kwh", "consumo_facturado_trimestral_kwh", "consumo_facturado_irregular_kwh"]
        quality_rows = []
        for item in self.quality["monthly_quality"]:
            tc2 = item["tc2"]
            quality_rows.append({"periodo": item["periodo"], "rows": tc2["rows"], "valid": tc2["valid_rows"],
                "duplicates": tc2["duplicate_rows"], "invalid": tc2["invalid_rows"], "unmatched": tc2["tc1_unmatched_rows"]})
        default_reference = float(self.prediction.iloc[:31].sum()/1000)
        return {"history": records(daily[history_cols]), "quality": quality_rows,
            "data_sha256": self.manifest["data_sha256"], "model_sha256": self.manifest["model_sha256"],
            "artifact_version": self.manifest["artifact_version"], "source_run_id": self.manifest["source_run_id"],
            "default_reference_mwh": default_reference, "families": records(self.families),
            "forecast_period": {"start": self.dates.min().strftime("%Y-%m-%d"), "end": self.dates.max().strftime("%Y-%m-%d")},
            "importance": self.manifest["importance"], "importance_scope": self.manifest["importance_scope"],
            "quality_checks": self.quality["output_checks"],
            "iterations": records(pd.read_csv(EVIDENCE / "diario_iteraciones.csv")),
            "manifest": self.manifest}

    def explanation(self, day):
        date = pd.Timestamp(day)
        if date not in self.dates:
            raise ValueError(f"Seleccione una fecha entre {self.dates.min():%Y-%m-%d} y {self.dates.max():%Y-%m-%d}.")
        return {"date": date.strftime("%Y-%m-%d"), **explain_path(self.model, self.feature_rows.loc[[date]])}


def export_zip(scenario):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("predicciones.csv", pd.DataFrame(scenario["rows"]).to_csv(index=False).encode("utf-8-sig"))
        archive.writestr("corrida.json", json.dumps(scenario, ensure_ascii=False, indent=2, allow_nan=False))
        archive.writestr("LEEME.txt", "LEMANI | Pronóstico de demanda\n\n"
            "Las cifras del CSV se expresan en kWh. El volumen de referencia de corrida.json está en MWh.\n"
            "Diferencia = referencia - pronóstico. Porcentaje = diferencia / referencia; no definido si referencia = 0.\n"
            "El ID depende de los parámetros y hashes; created_utc registra la fecha de generación.\n"
            "Los intervalos son diarios y exploratorios; no sumarlos para inferir cobertura mensual.\n"
            "Sin recomendación comercial ni validación económica.\n")
    return stream.getvalue()
