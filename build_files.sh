#!/usr/bin/env bash
set -e

pip install -r requirements.txt

# Tell Django this is the build phase — use SQLite, skip DB connection
export VERCEL_BUILD=1

python manage.py collectstatic --noinput

mkdir -p staticfiles_build/static
cp -r staticfiles/. staticfiles_build/static/
