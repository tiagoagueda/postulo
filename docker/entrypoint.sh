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
# This is the one request Postulo makes with nobody asking for it, to
# download.geonames.org, through the same guarded client as every other. A start-up
# without a network is a map without dots until one returns, and the map page says the
# dataset is not there; the rest of Postulo does not care. POSTULO_SKIP_GEONAMES=1
# turns the download off (put geonames-cities1000.txt and geonames-countryinfo.txt in
# that directory by hand to have the map anyway). POSTULO_GEONAMES_POSTCODES=1 also downloads
# the postal codes (#702), so an address is placed at its postcode rather than its town.
geonames_dir="${POSTULO_GEOLOCATIONS_DIR:-/app/data/geonames}"
if [ "${POSTULO_SKIP_GEONAMES:-}" != "1" ] && [ ! -f "$geonames_dir/geonames-cities1000.txt" ]; then
    mkdir -p "$geonames_dir"
    python manage.py fetch_geonames || \
        echo "Postulo: the GeoNames city dataset could not be fetched; locations are not placed on the map until it is"
fi

# A quick sanity check on the configuration, so a misconfigured instance says so on
# start-up rather than at the first request.
python manage.py check --deploy --fail-level ERROR

# gunicorn trusts X-Forwarded-* from its own list, which is 127.0.0.1 unless told otherwise,
# while Postulo's middleware trusts POSTULO_TRUSTED_PROXIES. Two lists that disagree are one
# that is wrong somewhere, so gunicorn is given the same one (#824). An operator who sets
# FORWARDED_ALLOW_IPS themselves keeps what they set.
if [ -z "${FORWARDED_ALLOW_IPS:-}" ] && [ -n "${POSTULO_TRUSTED_PROXIES:-}" ]; then
    FORWARDED_ALLOW_IPS="$POSTULO_TRUSTED_PROXIES"
    export FORWARDED_ALLOW_IPS
fi

exec "$@"
