FROM debian:trixie-slim AS system

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/gemapi/bin:${PATH}"

WORKDIR /app

# Python et GDAL proviennent de la même distribution. Le venv voit les
# bindings GDAL et les bibliothèques associées installés par Debian.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates gdal-bin python3-gdal python3-venv util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages /opt/gemapi \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && install -d -m 700 -o appuser -g appuser /var/lib/gemapi/media

FROM system AS dependencies

COPY pyproject.toml /build-metadata/digues-app.toml
COPY webapp/pyproject.toml /build-metadata/digues-webapp.toml
COPY docker/build_requirements.py /build-metadata/build_requirements.py
RUN python -m pip install "setuptools>=68" "wheel>=0.42,<1" \
    && python /build-metadata/build_requirements.py \
    && python -m pip install -r /build-metadata/requirements.txt

FROM dependencies AS app-wheels

COPY pyproject.toml README.md ./
COPY digues_app ./digues_app
COPY webapp/pyproject.toml webapp/setup.py webapp/README.md ./webapp/
COPY webapp/backend ./webapp/backend
COPY webapp/frontend ./webapp/frontend
RUN python -m pip wheel --no-deps --no-build-isolation --wheel-dir=/app-wheels . ./webapp

FROM system AS runtime

ENV MPLCONFIGDIR=/tmp/matplotlib

COPY --from=dependencies /opt/gemapi /opt/gemapi
COPY --from=app-wheels /app-wheels /app-wheels
RUN python -m pip install --no-deps --no-index --find-links=/app-wheels digues-app digues-webapp \
    && python -m pip check \
    && python -c "from osgeo import gdal; assert gdal.VersionInfo(); import matplotlib.pyplot" \
    && chown -R appuser:appuser /tmp/matplotlib

COPY LICENSE NOTICE ./
COPY docker/entrypoint.sh /usr/local/bin/gemapi-entrypoint.sh
RUN chmod 755 /usr/local/bin/gemapi-entrypoint.sh

ARG GEMAPI_BUILD_REVISION=unknown
ENV GEMAPI_BUILD_REVISION=${GEMAPI_BUILD_REVISION}

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).read()"

ENTRYPOINT ["/usr/local/bin/gemapi-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "digues_webapp.app:app", "--host", "0.0.0.0", "--port", "8000"]
