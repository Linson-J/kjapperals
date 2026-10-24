#!/usr/bin/env bash
set -e

pip install -r requirements.txt

python manage.py collectstatic --noinput

mkdir -p staticfiles_build/static
cp -r staticfiles/. staticfiles_build/static/
