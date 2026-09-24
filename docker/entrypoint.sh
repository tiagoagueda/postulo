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

# The offline city table the map places company locations from: fetched once, into the
# data volume where POSTULO_GEOLOCATIONS_DIR points, and kept there across upgrades.
# A start-up without a network is a map without dots until one returns, and the map
# page says the dataset is not there; the rest of Postulo does not care.
if [ ! -f /app/data/geonames/geonames-cities1000.txt ]; then
    mkdir -p /app/data/geonames
    python manage.py fetch_geonames || \
        echo "Postulo: the GeoNames city dataset could not be fetched; locations are not placed on the map until it is"
fi

# A quick sanity check on the configuration, so a misconfigured instance says so on
# start-up rather than at the first request.
python manage.py check --deploy --fail-level ERROR

exec "$@"
