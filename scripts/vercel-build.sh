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

first_database_url() {
  for name in DATABASE_URL POSTGRES_URL POSTGRES_URL_NON_POOLING; do
    local value="${!name:-}"
    if [ -n "${value}" ]; then
      echo "${value}"
      return 0
    fi
  done
  return 1
}

normalize_database_url() {
  local value="$1"
  value="${value#DATABASE_URL=}"
  value="${value#POSTGRES_URL=}"
  value="${value#POSTGRES_URL_NON_POOLING=}"
  value="${value#\"}"
  value="${value%\"}"
  value="${value#\'}"
  value="${value%\'}"
  echo "${value}"
}

require_env "DJANGO_SECRET_KEY"
database_url="$(first_database_url || true)"
database_url="$(normalize_database_url "${database_url}")"

if [ -z "${database_url}" ]; then
  echo "Missing required environment variable: DATABASE_URL" >&2
  echo "You can also use POSTGRES_URL or POSTGRES_URL_NON_POOLING from Vercel Storage." >&2
  missing=1
fi

if [ "${missing}" -ne 0 ]; then
  echo "Vercel build stopped before deploying a broken Django runtime." >&2
  echo "Set the missing variables in Vercel Project Settings > Environment Variables, then redeploy." >&2
  exit 1
fi

case "${database_url}" in
  sqlite:*|*"paste-real"*|*"username:password@hostname"*|*"user:password@host"*|*"<your"*|*"<hosted"*)
    echo "DATABASE_URL must be a real hosted PostgreSQL connection string for Vercel." >&2
    exit 1
    ;;
  postgres:*|postgresql:*|pgsql:*|postgis:*|timescale:*|timescalegis:*)
    ;;
  *)
    echo "DATABASE_URL must start with postgres:// or postgresql:// for Vercel." >&2
    exit 1
    ;;
esac

export DJANGO_DEBUG="${DJANGO_DEBUG:-false}"
export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-.vercel.app}"
export DATABASE_URL="${database_url}"
export DATABASE_SSL_REQUIRE="${DATABASE_SSL_REQUIRE:-true}"

python manage.py check --deploy
python manage.py collectstatic --noinput
python manage.py migrate --noinput
