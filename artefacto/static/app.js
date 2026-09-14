"use strict";

const $ = (id) => document.getElementById(id);
const state = { overview: null, scenario: null, applied: null, busy: false, explanationToken: 0 };
const number = (value, digits = 1) => value == null ? "No definido" : new Intl.NumberFormat("es-CO", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
const percent = (value, digits = 2) => value == null ? "No definido" : `${number(value * 100, digits)} %`;
const shortDate = (date) => new Intl.DateTimeFormat("es-CO", { day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(`${date}T12:00:00Z`));
const yearOf = (date) => String(date).slice(0, 4);
const monthLabel = (code) => new Intl.DateTimeFormat("es-CO", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${code}-15T12:00:00Z`));
const setText = (id, text) => { $(id).textContent = text; };
const colors = { forest: "#5f6538", ink: "#3d402d", amber: "#8a6a2b", solar: "#c5ab50", lime: "#aeb66f", grey: "#8b8c79" };
const plotConfig = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"], toImageButtonOptions: { format: "png", filename: "lemani-pronostico", scale: 2 } };
const plotLayout = {
  font: { family: "Segoe UI, Arial, sans-serif", size: 11, color: "#667065" },
  paper_bgcolor: "transparent", plot_bgcolor: "transparent",
  margin: { t: 25, r: 10, b: 70, l: 55 }, hovermode: "x unified",
  xaxis: { showgrid: false, zeroline: false, tickformat: "%d %b", nticks: 6, automargin: true },
  yaxis: { gridcolor: "#e9eddf", zeroline: false, automargin: true },
  legend: { orientation: "h", y: -0.2, x: 0, font: { size: 10 } },
};

function errorMessage(error) {
  if (error instanceof TypeError && error.message.includes("fetch")) return "No fue posible contactar al servidor. Revisa la conexión y vuelve a intentar.";
  return error.message || "No fue posible completar la operación.";
}

function notify(message = "") {
  $("notification").hidden = !message;
  setText("notification", message);
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? { credentials: "same-origin" } : {
    method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("Se requiere acceso del equipo. Recarga la página e ingresa las credenciales del alojamiento.");
    const payload = await response.json().catch(() => ({}));
    const detail = typeof payload.detail === "string" ? payload.detail : response.status === 422 ? "Revisa el modelo, el horizonte y el volumen de referencia. Solo se admiten valores finitos y no negativos." : `El servidor devolvió un error (${response.status}). Intenta de nuevo o contacta al responsable técnico.`;
    throw new Error(detail);
  }
  return response;
}

function tableRows(id, rows) {
  const fragment = document.createDocumentFragment();
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of row) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    }
    fragment.append(tr);
  }
  $(id).replaceChildren(fragment);
}

function navigate(view) {
  const allowed = ["pronostico", "calidad"];
  if (!allowed.includes(view)) view = "pronostico";
  document.querySelectorAll(".view").forEach((element) => { element.hidden = element.id !== view; });
  document.querySelectorAll(".nav-item").forEach((element) => {
    element.classList.toggle("active", element.dataset.view === view);
    if (element.dataset.view === view) element.setAttribute("aria-current", "page");
    else element.removeAttribute("aria-current");
  });
  if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  requestAnimationFrame(() => {
    document.querySelectorAll(`#${view} .js-plotly-plot`).forEach((element) => Plotly.Plots.resize(element));
  });
}

function setBusy(value) {
  state.busy = value;
  for (const id of ["calculate", "horizon", "method", "reference"]) $(id).disabled = value;
  $("calculate").textContent = value ? "Calculando…" : "Actualizar escenario ↗";
  for (const id of ["download-csv", "download-zip"]) $(id).disabled = value || !state.scenario;
  $("scenario-form").setAttribute("aria-busy", String(value));
}

function getParameters() {
  return { method: $("method").value, horizon: Number($("horizon").value), reference_mwh: Number($("reference").value) };
}

function validateParameters(parameters) {
  if (![31, 61, 92].includes(parameters.horizon) || !["extra_trees", "estacional_7", "media_movil_7", "ultimo_valor"].includes(parameters.method) || !Number.isFinite(parameters.reference_mwh) || parameters.reference_mwh < 0 || parameters.reference_mwh > 1e9) throw new Error("Parámetros no admitidos para este pronóstico.");
}

async function applyScenario(parameters) {
  if (state.busy) throw new Error("Espera a que finalice la operación actual.");
  validateParameters(parameters);
  notify();
  setBusy(true);
  try {
    const scenario = await (await api("/api/scenario", parameters)).json();
    await renderScenario(scenario);
    state.scenario = scenario;
    state.applied = { ...parameters };
    $("method").value = parameters.method;
    $("horizon").value = String(parameters.horizon);
    $("reference").value = String(parameters.reference_mwh);
    setText("dirty-status", "Escenario aplicado");
    return scenario;
  } catch (error) {
    notify(errorMessage(error));
    throw error;
  } finally {
    setBusy(false);
  }
}

async function renderScenario(scenario) {
  setText("scenario-caption", `${scenario.method_label} · ${shortDate(scenario.start)} de ${yearOf(scenario.start)} al ${shortDate(scenario.end)} de ${yearOf(scenario.end)} · ${scenario.horizon} días`);
  setText("kpi-forecast", number(scenario.forecast_mwh, 0));
  setText("kpi-wape", number(scenario.daily_average_mwh, 0));
  setText("kpi-mae", "MWh esperados por día");
  setText("kpi-improvement", percent(scenario.validation_metrics.wape));
  const difference = scenario.reference_difference_mwh;
  setText("balance-value", `${difference >= 0 ? "+" : "−"}${number(Math.abs(difference), 0)} MWh`);
  setText("balance-label", difference >= 0 ? "Referencia por encima del pronóstico" : "Referencia por debajo del pronóstico");
  setText("balance-reference", `${number(scenario.reference_mwh, 0)} MWh`);
  setText("balance-forecast", `${number(scenario.forecast_mwh, 0)} MWh`);
  setText("balance-pct", percent(scenario.reference_difference_pct));
  $("balance-fill").style.width = `${scenario.reference_mwh ? Math.min(100, scenario.forecast_mwh / scenario.reference_mwh * 100) : 0}%`;
  const hasInterval = scenario.interval.available;
  setText("interval-summary", hasInterval ? `El rango diario usa un nivel nominal del 90 % y tuvo cobertura de ${percent(scenario.interval.validation_coverage, 1)} en julio. Ancho medio: ${number(scenario.interval.mean_width_kwh / 1000)} MWh. La cobertura de estos meses se medirá cuando llegue la demanda real.` : "Este baseline no tiene un intervalo calibrado. No se muestra una banda de confianza inventada.");
  setText("failure-date", `${shortDate(scenario.peak_day.date)} de ${yearOf(scenario.peak_day.date)}`);
  setText("failure-error", `${number(scenario.peak_day.prediction_kwh / 1000)} MWh pronosticados.`);
  setText("scenario-conclusion", `${scenario.method_label} proyecta ${number(scenario.forecast_mwh, 0)} MWh en el periodo. Su WAPE de validación fue ${percent(scenario.validation_metrics.wape)} en julio, con una mejora de ${percent(scenario.validation_improvement_wape, 1)} frente al baseline semanal. El error futuro todavía no es observable.`);
  setText("run-id", `Corrida ${scenario.run_id}`);
  tableRows("daily-table", scenario.rows.map((r) => [r.fecha, number(r.prediccion_kwh / 1000, 2), number(r.baseline_kwh / 1000, 2), r.inferior_kwh == null ? "No aplica" : number(r.inferior_kwh / 1000, 2), r.superior_kwh == null ? "No aplica" : number(r.superior_kwh / 1000, 2)]));
  const x = scenario.rows.map((r) => r.fecha);
  const traces = [];
  if (hasInterval) {
    traces.push({ x, y: scenario.rows.map((r) => r.inferior_kwh / 1000), mode: "lines", line: { width: 0 }, hoverinfo: "skip", showlegend: false });
    traces.push({ x, y: scenario.rows.map((r) => r.superior_kwh / 1000), mode: "lines", fill: "tonexty", fillcolor: "rgba(189,215,108,0.24)", line: { width: 0 }, name: "Rango exploratorio", hoverinfo: "skip" });
  }
  traces.push({ x, y: scenario.rows.map((r) => r.baseline_kwh / 1000), mode: "lines", name: "Estacional 7 días", line: { color: colors.amber, width: 1.7, dash: "dot" } });
  traces.push({ x, y: scenario.rows.map((r) => r.prediccion_kwh / 1000), mode: "lines+markers", name: scenario.method_label, line: { color: colors.forest, width: 2.8 }, marker: { size: 4 } });
  await Plotly.react("forecast-chart", traces, plotLayout, plotConfig);
  const previousDate = $("explain-date").value;
  $("explain-date").replaceChildren(...scenario.rows.map((r) => new Option(shortDate(r.fecha), r.fecha)));
  $("explain-date").value = scenario.rows.some((r) => r.fecha === previousDate) ? previousDate : scenario.start;
  $("explain-date").disabled = !hasInterval;
  if (hasInterval) await renderExplanation($("explain-date").value);
  else {
    state.explanationToken++;
    Plotly.purge("explanation-chart");
    $("explanation-chart").hidden = true;
    setText("explanation-method", "La descomposición por árboles solo aplica a Extra Trees.");
    setText("explanation-summary", scenario.method === "estacional_7" ? "Cada día repite el valor de siete días atrás; después de la primera semana se reutiliza el patrón pronosticado." : scenario.method === "media_movil_7" ? "Cada día utiliza la media de los siete valores anteriores, incorporando sus propias predicciones recursivamente." : `Todos los días repiten el último valor observado al cierre de ${scenario.training_end}.`);
    tableRows("explanation-table", []);
  }
}

async function renderExplanation(day) {
  const token = ++state.explanationToken;
  try {
    const result = await (await api(`/api/explanation/${encodeURIComponent(day)}`)).json();
    if (token !== state.explanationToken) return;
    $("explanation-chart").hidden = false;
    setText("explanation-method", result.method);
    const top = result.contributions.slice(0, 6);
    const others = result.contributions.slice(6).reduce((sum, r) => sum + r.kwh, 0);
    const bars = [...top, { label: "Otras variables (neto)", kwh: others }].reverse();
    await Plotly.react("explanation-chart", [{ type: "bar", orientation: "h", x: bars.map((r) => r.kwh / 1000), y: bars.map((r) => r.label), marker: { color: bars.map((r) => r.kwh >= 0 ? colors.forest : colors.amber) }, hovertemplate: "%{y}: %{x:.2f} MWh<extra></extra>" }], { ...plotLayout, hovermode: "closest", showlegend: false, margin: { t: 12, r: 20, b: 42, l: 175 }, xaxis: { title: { text: "Aporte a la predicción (MWh)", font: { size: 10 } }, gridcolor: "#e9eddf", zerolinecolor: "#afbaa4", automargin: true }, yaxis: { automargin: true } }, plotConfig);
    const net = result.contributions.reduce((sum, r) => sum + r.kwh, 0);
    setText("explanation-summary", `Valor base ${number(result.base_kwh / 1000, 2)} MWh + aportes netos ${number(net / 1000, 2)} MWh = pronóstico ${number(result.prediction_kwh / 1000, 2)} MWh. Describe el cálculo del modelo; no explica causas físicas de la demanda.`);
    tableRows("explanation-table", result.contributions.map((r) => [r.label, number(r.kwh / 1000, 3)]));
  } catch (error) {
    if (token !== state.explanationToken) return;
    $("explanation-chart").hidden = true;
    tableRows("explanation-table", []);
    setText("explanation-summary", "La explicación no está disponible. No se muestran aportes de otra fecha.");
    notify(errorMessage(error));
  }
}

async function renderQuality() {
  if (!state.overview) return;
  const month = $("quality-month").value;
  const field = $("billing-type").value;
  const rows = state.overview.history.filter((r) => month === "all" || r.fecha.slice(0, 7) === month);
  const x = rows.map((r) => r.fecha);
  await Plotly.react("quality-chart", [
    { x, y: rows.map((r) => r.demanda_total_kwh / 1000), name: "Demanda real total", mode: "lines", line: { color: colors.ink, width: 2 } },
    { x, y: rows.map((r) => r[field] / 1000), name: "Facturado calendarizado", mode: "lines", line: { color: colors.forest, width: 2 } },
  ], { ...plotLayout, yaxis: { ...plotLayout.yaxis, title: { text: "MWh / día", font: { size: 10 } } } }, plotConfig);
  tableRows("quality-table", rows.map((r) => [r.fecha, number(r.demanda_total_kwh / 1000, 2), number(r[field] / 1000, 2)]));
}

async function renderOverview(data) {
  setText("quality-days", number(data.quality_checks.primary_unique_dates, 0));
  setText("quality-periods", number(data.quality.length, 0));
  setText("quality-duplicates", number(data.quality.reduce((sum, r) => sum + r.duplicates, 0), 0));
  setText("quality-privacy", data.quality_checks.privacy.passed ? "Sin hallazgos" : "Revisar");
  setText("reconciliation", `Diferencia absoluta entre consumo calendarizado del portafolio y suma de segmentos: ${Math.abs(data.quality_checks.reconciliation_difference_kwh).toExponential(2)} kWh. Es una conciliación interna, no una validación contra demanda medida.`);
  tableRows("quality-audit", data.quality.map((r) => [r.periodo, number(r.rows, 0), number(r.duplicates, 0), number(r.invalid, 0), number(r.unmatched, 0)]));
  const months = [...new Set(data.history.map((row) => row.fecha.slice(0, 7)))];
  $("quality-month").replaceChildren(new Option("Todo el periodo", "all"), ...months.map((code) => new Option(monthLabel(code), code)));
  await renderQuality();
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function initialize() {
  if (state.busy) return;
  notify();
  $("loading").hidden = false;
  $("calculate").disabled = true;
  try {
    if (typeof Plotly === "undefined") throw new Error("No cargó la librería local de gráficos. Recarga la página o verifica el servidor.");
    const overview = await (await api("/api/overview")).json();
    state.overview = overview;
    await renderOverview(overview);
    $("reference").value = String(Number(overview.default_reference_mwh.toFixed(3)));
    $("method").value = "extra_trees";
    $("horizon").value = "31";
    await applyScenario(getParameters());
  } catch (error) {
    notify(errorMessage(error));
    $("calculate").disabled = !state.overview;
  } finally {
    $("loading").hidden = true;
  }
}

document.querySelectorAll(".nav-item").forEach((element) => element.addEventListener("click", () => navigate(element.dataset.view)));
window.addEventListener("hashchange", () => navigate(location.hash.slice(1)));
$("scenario-form").addEventListener("submit", (event) => {
  event.preventDefault();
  applyScenario(getParameters()).catch(() => {});
});
$("scenario-form").addEventListener("input", () => setText("dirty-status", "Cambios pendientes de aplicar"));
$("horizon").addEventListener("change", () => {
  setText("reference-hint", "La referencia conserva el valor que ingresaste. Ajústala al horizonte elegido antes de aplicar; no se prorratea automáticamente.");
});
$("explain-date").addEventListener("change", () => renderExplanation($("explain-date").value));
$("quality-month").addEventListener("change", () => renderQuality().catch((error) => notify(errorMessage(error))));
$("billing-type").addEventListener("change", () => renderQuality().catch((error) => notify(errorMessage(error))));
$("download-csv").addEventListener("click", () => {
  if (!state.scenario) return;
  const keys = Object.keys(state.scenario.rows[0]);
  const csv = [keys.join(","), ...state.scenario.rows.map((r) => keys.map((key) => r[key]).join(","))].join("\r\n");
  download(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }), `lemani-${state.scenario.run_id}.csv`);
});
$("download-zip").addEventListener("click", async () => {
  if (!state.applied || state.busy) return;
  const applied = { ...state.applied };
  const id = state.scenario.run_id;
  setBusy(true);
  notify();
  try { download(await (await api("/api/export", applied)).blob(), `lemani-${id}.zip`); }
  catch (error) { notify(errorMessage(error)); }
  finally { setBusy(false); }
});

function registerTools() {
  const context = document.modelContext;
  if (!context?.registerTool) return;
  const lifecycle = new AbortController();
  window.addEventListener("pagehide", () => lifecycle.abort(), { once: true });
  const tools = [{
    name: "leer_escenario_demanda", title: "Leer escenario aplicado", description: "Lee el pronóstico futuro visible y su evidencia de validación sin cambiarlo.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, untrustedContentHint: false },
    execute() {
      if (!state.scenario) throw new Error("Todavía no hay un escenario aplicado.");
      const s = state.scenario;
      return { run_id: s.run_id, method: s.method, horizon: s.horizon, forecast_mwh: s.forecast_mwh, reference_mwh: s.reference_mwh, validation_metrics: s.validation_metrics, limitations: s.limitations };
    },
  }, {
    name: "aplicar_escenario_demanda", title: "Aplicar escenario de demanda", description: "Calcula y muestra un pronóstico futuro. Cambia los controles y resultados visibles; no compra energía ni guarda datos.",
    inputSchema: { type: "object", properties: { method: { type: "string", enum: ["extra_trees", "estacional_7", "media_movil_7", "ultimo_valor"] }, horizon: { type: "integer", enum: [31, 61, 92] }, reference_mwh: { type: "number", minimum: 0, maximum: 1e9 } }, required: ["method", "horizon", "reference_mwh"], additionalProperties: false }, annotations: { readOnlyHint: false, untrustedContentHint: false },
    async execute(input) {
      if (!input || Object.keys(input).sort().join(",") !== "horizon,method,reference_mwh") throw new Error("Se requieren modelo, horizonte y referencia, sin campos adicionales.");
      validateParameters(input);
      navigate("pronostico");
      const s = await applyScenario(input);
      return { run_id: s.run_id, method: s.method, horizon: s.horizon, forecast_mwh: s.forecast_mwh, reference_difference_mwh: s.reference_difference_mwh, validation_wape: s.validation_metrics.wape, future_forecast: true };
    },
  }];
  for (const tool of tools) {
    try { Promise.resolve(context.registerTool(tool, { signal: lifecycle.signal })).catch(() => {}); }
    catch { }
  }
}

navigate(location.hash.slice(1));
registerTools();
initialize();
