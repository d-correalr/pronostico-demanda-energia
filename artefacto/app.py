from __future__ import annotations

import base64
import binascii
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import plotly
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .service import Engine, export_zip

STATIC = Path(__file__).parent / "static"


class ScenarioInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    horizon: Literal[31, 61, 92] = 31
    method: Literal["extra_trees", "estacional_7", "media_movil_7", "ultimo_valor"] = "extra_trees"
    reference_mwh: float = Field(default=83000, ge=0, le=1e9)


@asynccontextmanager
async def lifespan(app):
    username, password = os.getenv("APP_USERNAME", ""), os.getenv("APP_PASSWORD", "")
    require_auth = os.getenv("REQUIRE_AUTH", "0") == "1"
    if require_auth and (not username or len(password) < 12):
        raise RuntimeError("Configure APP_USERNAME y APP_PASSWORD de al menos 12 caracteres en los secretos del alojamiento.")
    if bool(username) != bool(password):
        raise RuntimeError("Configure ambos valores de acceso o ninguno para uso local.")
    app.state.credentials = (username, password) if username and password else None
    app.state.engine = Engine()
    yield


app = FastAPI(title="LEMANI | Laboratorio de demanda", version="1.2.0", lifespan=lifespan,
    docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    details = [{"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
    return JSONResponse({"detail": details}, status_code=422)


@app.middleware("http")
async def security(request: Request, call_next):
    credentials = getattr(request.app.state, "credentials", None)
    if credentials and request.url.path != "/health":
        try:
            scheme, encoded = request.headers.get("authorization", "").split(" ", 1)
            supplied = base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)
            valid = scheme.lower() == "basic" and len(supplied) == 2
            valid = valid and secrets.compare_digest(supplied[0].encode(), credentials[0].encode()) and secrets.compare_digest(supplied[1].encode(), credentials[1].encode())
        except (ValueError, UnicodeDecodeError, binascii.Error):
            valid = False
        if not valid:
            return Response("Acceso privado. Ingrese las credenciales del equipo.", status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="LEMANI", charset="UTF-8"', "Cache-Control": "no-store"})
    if request.method == "POST" and request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "private, max-age=300"
    return response


@app.get("/health")
def health(request: Request):
    return {"status": "ready" if hasattr(request.app.state, "engine") else "starting"}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/vendor/plotly.min.js")
def plotly_bundle():
    return FileResponse(Path(plotly.__file__).parent / "package_data" / "plotly.min.js", media_type="text/javascript")


@app.get("/api/overview")
def overview(request: Request):
    return request.app.state.engine.overview()


@app.post("/api/scenario")
def scenario(body: ScenarioInput, request: Request):
    return request.app.state.engine.scenario(**body.model_dump())


@app.post("/api/export")
def export(body: ScenarioInput, request: Request):
    result = request.app.state.engine.scenario(**body.model_dump())
    return Response(export_zip(result), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="lemani-{result["run_id"]}.zip"'})


@app.get("/api/explanation/{day}")
def explanation(day: str, request: Request):
    try:
        return request.app.state.engine.explanation(day)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


app.mount("/static", StaticFiles(directory=STATIC), name="static")
