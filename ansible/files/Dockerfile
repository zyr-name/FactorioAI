# Pin the game version so restarting/rebuilding does not upgrade saves unexpectedly.
ARG FACTORIO_VERSION=2.0.77
FROM factoriotools/factorio:${FACTORIO_VERSION}

COPY config/server-settings.example.json /defaults/server-settings.json
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/factorio-entrypoint

ENV DLC_SPACE_AGE=false
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD rcon /version >/dev/null 2>&1 || exit 1
ENTRYPOINT ["/usr/local/bin/factorio-entrypoint"]
