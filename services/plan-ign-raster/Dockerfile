FROM node:22-bookworm-slim AS node-runtime

# MapLibre Native 6.4.1 is linked against glibc >= 2.38 and ICU 74.
# Ubuntu 24.04 matches the VPS runtime; the Node binary itself runs on it.
FROM ubuntu:24.04

ENV NODE_ENV=production \
    LIBGL_ALWAYS_SOFTWARE=1 \
    MESA_GL_VERSION_OVERRIDE=4.5 \
    HOST=0.0.0.0 \
    PORT=8788 \
    CACHE_ROOT=/var/cache/digues-app/basemaps

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates libcurl4t64 libjpeg-turbo8 libuv1t64 libwebp7 libicu74 \
       libegl1 libgl1 libgl1-mesa-dri libopengl0 \
       libx11-6 libxext6 libxrender1 libxrandr2 libxi6 libxkbcommon0 \
       libfontconfig1 libfreetype6 tini xauth xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin renderer \
    && mkdir -p /var/cache/digues-app/basemaps \
    && chown -R renderer:renderer /var/cache/digues-app

RUN mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

WORKDIR /srv/plan-ign-raster
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY *.mjs ./
COPY start.sh ./

USER renderer
EXPOSE 8788
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:8788/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "./start.sh"]
