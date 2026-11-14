#!/bin/sh
# Bring the database up to date, then hand over to whatever was asked for.
#
# Migrations run on every start rather than as a separate step somebody has to remember.
# For a single-instance application that is the right trade: an upgrade is "pull the new
# image and restart", and a half-migrated database because a step was skipped is a much
# worse failure than a few seconds of start-up.
set -eu

# Whether a step is switched off: its variable is 0. The names before 0.6 said it the other way
# round (POSTULO_SKIP_MIGRATE=1); they still work for this minor, with a warning, and an
# answer under the new name wins over one under the old (#1186).
is_off() {
    new_name=$1
    old_name=$2
    eval "new=\${$new_name:-}"
    eval "old=\${$old_name:-}"
    if [ -n "$old" ]; then
        echo "Postulo: $old_name is the old name of $new_name, which is 0 where it was 1; the old name stops working in 0.7" >&2
    fi
    [ "$new" = "0" ] || { [ -z "$new" ] && [ "$old" = "1" ]; }
}

if ! is_off POSTULO_CONTAINER_MIGRATE POSTULO_SKIP_MIGRATE; then
    echo "Postulo: applying migrations"
    python manage.py migrate --noinput
fi

# Plugins live on the data volume, and the environment in this image is brand new after
# an upgrade. Anything the volume's record lists but the environment lacks is installed
# again here, before the first request.
if ! is_off POSTULO_CONTAINER_PLUGIN_SYNC POSTULO_SKIP_PLUGIN_SYNC; then
    python manage.py plugins sync || echo "Postulo: some plugins could not be restored"
fi

# The offline city table the map places company locations from: fetched once, into the
# data volume where POSTULO_GEOLOCATIONS_DIR points, and kept there across upgrades.
# This is the one request Postulo makes with nobody asking for it, to
# download.geonames.org, through the same guarded client as every other. A start-up
# without a network is a map without dots until one returns, and the map page says the
# dataset is not there; the rest of Postulo does not care. POSTULO_CONTAINER_GEONAMES_FETCH=0
# turns the download off (put geonames-cities1000.txt and geonames-countryinfo.txt in
# that directory by hand to have the map anyway). POSTULO_GEONAMES_POSTCODES=1 also downloads
# the postal codes (#702), so an address is placed at its postcode rather than its town.
#
# What the download leaves is the lookup built from the city table, geonames-cities1000.sqlite,
# and not the table (#1127), so either one there means there is nothing to fetch: the
# lookup of an earlier start, or a table put there by hand, which is indexed at its first use.
geonames_dir="${POSTULO_GEOLOCATIONS_DIR:-/app/data/geonames}"
if ! is_off POSTULO_CONTAINER_GEONAMES_FETCH POSTULO_SKIP_GEONAMES \
    && [ ! -f "$geonames_dir/geonames-cities1000.sqlite" ] \
    && [ ! -f "$geonames_dir/geonames-cities1000.txt" ]; then
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
