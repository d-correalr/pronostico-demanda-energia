FROM python:3.13.7-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOST=0.0.0.0 PORT=7860 REQUIRE_AUTH=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /app
COPY requirements-app.txt ./
RUN pip install --no-cache-dir -r requirements-app.txt && useradd --create-home --uid 1000 lemani
COPY --chown=lemani:lemani artefacto ./artefacto
COPY --chown=lemani:lemani data/processed ./data/processed
COPY --chown=lemani:lemani data/evidence ./data/evidence
RUN chown lemani:lemani /app
USER lemani
RUN python -m artefacto.prepare
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','7860')+'/health',timeout=4)"
CMD ["python", "-m", "artefacto"]
