#!/bin/sh
# Bring the database up to date, then hand over to whatever was asked for.
#
# Migrations run on every start rather than as a separate step somebody has to remember.
# For a single-instance application that is the right trade: an upgrade is "pull the new
# image and restart", and a half-migrated database because a step was skipped is a much
# worse failure than a few seconds of start-up.
set -eu

if [ "${POSTULO_SKIP_MIGRATE:-}" != "1" ]; then
    echo "Postulo: applying migrations"
    python manage.py migrate --noinput
fi

# Plugins live on the data volume, and the environment in this image is brand new after
# an upgrade. Anything the volume's record lists but the environment lacks is installed
# again here, before the first request.
if [ "${POSTULO_SKIP_PLUGIN_SYNC:-}" != "1" ]; then
    python manage.py plugins sync || echo "Postulo: some plugins could not be restored"
fi

# A quick sanity check on the configuration, so a misconfigured instance says so on
# start-up rather than at the first request.
python manage.py check --deploy --fail-level ERROR

exec "$@"
