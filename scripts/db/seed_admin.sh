#!/usr/bin/env bash
# Seed the initial admin user.
# Run ONCE after first deploy — subsequent runs are no-ops (ON CONFLICT DO NOTHING).
#
# Required env vars:
#   DATABASE_URL          — postgres connection string
#   ADMIN_INITIAL_USER    — username (default: admin)
#   ADMIN_INITIAL_PASSWORD — strong password (minimum 16 chars)
#
# Example:
#   ADMIN_INITIAL_PASSWORD="$(openssl rand -base64 24)" \
#   DATABASE_URL="postgresql://..." \
#   ./scripts/db/seed_admin.sh

set -euo pipefail

DB_URL="${DATABASE_URL:?DATABASE_URL is required}"
ADMIN_USER="${ADMIN_INITIAL_USER:-admin}"
ADMIN_PASS="${ADMIN_INITIAL_PASSWORD:?ADMIN_INITIAL_PASSWORD is required — generate with: openssl rand -base64 24}"

if [ ${#ADMIN_PASS} -lt 16 ]; then
    echo "ERROR: ADMIN_INITIAL_PASSWORD must be at least 16 characters" >&2
    exit 1
fi

# Validate username: alphanumeric + underscore only, preventing SQL injection
# even though we use parameterised binding below.
if ! echo "$ADMIN_USER" | grep -qE '^[a-zA-Z0-9_]{1,50}$'; then
    echo "ERROR: ADMIN_INITIAL_USER must be 1-50 alphanumeric/underscore characters" >&2
    exit 1
fi

# Resolve repo root (seed_admin.go lives at <repo>/scripts/seed_admin.go)
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Generate bcrypt hash using Go (golang.org/x/crypto/bcrypt, cost 12)
HASH=$(cd "$REPO_ROOT/scripts" && go run seed_admin.go "$ADMIN_PASS")

# Use psql --variable to pass values safely — psql substitutes :admin_user and
# :admin_hash as literal SQL values without shell interpolation risk.
psql "$DB_URL" \
    --variable="admin_user=$ADMIN_USER" \
    --variable="admin_hash=$HASH" \
    <<'SQL'
INSERT INTO admin_schema.admin_users (id, username, password_hash, role, must_change_password, created_at, updated_at)
VALUES (gen_random_uuid(), :'admin_user', :'admin_hash', 'superadmin', true, NOW(), NOW())
ON CONFLICT (username) DO NOTHING;
SQL

echo "Admin user '${ADMIN_USER}' seeded (must change password on first login)."
echo "Store this password in your secrets manager — it will not be shown again."
