#!/bin/sh
# Entrypoint de l’image gemapi.dev.
#
# Le conteneur peut démarrer en root uniquement pour préparer le répertoire des
# médias : un volume nommé neuf appartient à root, quel que soit le
# propriétaire du répertoire correspondant dans l'image. Le processus applicatif
# est ensuite exécuté avec l'UID/GID 10001 (appuser), sans privilège.
#
# Si le conteneur est démarré avec --user 10001, aucune préparation n'est
# effectuée et la commande est exécutée directement.
set -eu

APP_UID=10001
APP_GID=10001

if [ "$(id -u)" = "0" ]; then
    if [ -n "${MEDIA_ROOT:-}" ]; then
        mkdir -p "$MEDIA_ROOT"
        if [ "$(stat -c '%u' "$MEDIA_ROOT")" != "$APP_UID" ]; then
            chown -R "$APP_UID:$APP_GID" "$MEDIA_ROOT"
        fi
    fi
    # libpq cherche les certificats privés dans le répertoire du compte effectif.
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups env HOME=/home/appuser "$@"
fi

exec "$@"
