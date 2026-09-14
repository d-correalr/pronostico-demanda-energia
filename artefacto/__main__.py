import os

import uvicorn

from .model import ARTIFACTS

if not (ARTIFACTS / "manifest.json").exists():
    from .prepare import prepare
    prepare()

uvicorn.run("artefacto.app:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "7860")), workers=1)
