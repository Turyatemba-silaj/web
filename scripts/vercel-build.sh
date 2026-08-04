#!/usr/bin/env bash
set -euo pipefail

missing=0

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "Missing required environment variable: ${name}" >&2
    missing=1
  fi
}

require_env "DJANGO_SECRET_KEY"
require_env "DATABASE_URL"

if [ "${missing}" -ne 0 ]; then
  echo "Vercel build stopped before deploying a broken Django runtime." >&2
  echo "Set the missing variables in Vercel Project Settings > Environment Variables, then redeploy." >&2
  exit 1
fi

case "${DATABASE_URL}" in
  sqlite:*|*"paste-real"*|*"username:password@hostname"*|*"user:password@host"*|*"<your"*|*"<hosted"*)
    echo "DATABASE_URL must be a real hosted PostgreSQL connection string for Vercel." >&2
    exit 1
    ;;
esac

export DJANGO_DEBUG="${DJANGO_DEBUG:-false}"
export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-.vercel.app}"
export DATABASE_SSL_REQUIRE="${DATABASE_SSL_REQUIRE:-true}"

python manage.py check --deploy
python manage.py collectstatic --noinput
python manage.py migrate --noinput
