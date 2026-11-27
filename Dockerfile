FROM python:3.12-slim-trixie AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dépendances système lourdes : indépendantes du code applicatif.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gdal-bin \
        libgdal-dev \
    && python -m pip install --upgrade pip "setuptools>=68" "wheel>=0.42,<1" \
    && GDAL_VERSION="$(gdal-config --version)" \
    && python -m pip install --prefix=/install "GDAL==${GDAL_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

# Les déclarations seules déterminent les wheels et l'installation tierces.
# Les distributions locales sont exclues de cette résolution, puis construites
# séparément depuis les sources et le frontend de la révision courante.
COPY pyproject.toml /build-metadata/digues-app.toml
COPY webapp/pyproject.toml /build-metadata/digues-webapp.toml
COPY docker/build_requirements.py /build-metadata/build_requirements.py
RUN python /build-metadata/build_requirements.py \
    && python -m pip wheel --wheel-dir=/wheels -r /build-metadata/requirements.txt \
    && python -m pip install --prefix=/install --ignore-installed --no-index --find-links=/wheels \
        -r /build-metadata/requirements.txt

FROM dependencies AS app-wheels

WORKDIR /app
COPY pyproject.toml README.md ./
COPY digues_app ./digues_app
COPY webapp/pyproject.toml webapp/setup.py webapp/README.md ./webapp/
COPY webapp/backend ./webapp/backend
COPY webapp/frontend ./webapp/frontend
RUN python -m pip wheel --no-deps --no-build-isolation --wheel-dir=/app-wheels . ./webapp


FROM python:3.12-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

# gdal-bin installe la bibliothèque GDAL correspondant à l'image de base.
# Ne pas coder en dur un paquet libgdal<N> : son nom dépend de la distribution
# et casserait la construction dès que l'image de base évolue.
# util-linux fournit setpriv, utilisé par l'entrypoint pour abandonner les
# privilèges après la préparation du volume média.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gdal-bin \
        util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && install -d -m 700 -o appuser -g appuser /var/lib/gemapi/media

COPY --from=dependencies /install /usr/local

# Le cache de polices de matplotlib est construit pendant la fabrication de
# l'image : le répertoire reste accessible à l'utilisateur applicatif et le
# premier appel à la génération d'une fiche PDF ne paie pas cette construction.
# Cette étape doit suivre l'installation des dépendances Python.
RUN python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot" \
    && chmod -R 777 /tmp/matplotlib

COPY --from=app-wheels /app-wheels /app-wheels
RUN python -m pip install --no-deps --no-index --find-links=/app-wheels \
        digues-app digues-webapp \
    && python -m pip check

COPY LICENSE NOTICE ./
COPY docker/entrypoint.sh /usr/local/bin/gemapi-entrypoint.sh
RUN chmod 755 /usr/local/bin/gemapi-entrypoint.sh

# Provenance non secrète, appliquée après les installations et validations.
# Le runtime ne consulte jamais git ; seul le build explicite fixe cette valeur.
ARG GEMAPI_BUILD_REVISION=unknown
ENV GEMAPI_BUILD_REVISION=${GEMAPI_BUILD_REVISION}

EXPOSE 8000

# Vivacité du processus, indépendante des credentials et des bases métier.
# La disponibilité PostgreSQL doit être supervisée séparément par base.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).read()"

# L'entrypoint prépare le volume média s'il est monté, puis exécute la commande
# sous l'UID 10001.
ENTRYPOINT ["/usr/local/bin/gemapi-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "digues_webapp.app:app", "--host", "0.0.0.0", "--port", "8000"]
