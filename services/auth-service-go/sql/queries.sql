-- SQL queries used by auth-service-go (sqlc reference + documentation)
-- All user lookups filter is_deleted = false for soft-delete consistency.

-- name: GetUserByWallet :one
SELECT id, wallet_address, chain, display_name, risk_tolerance,
       preferred_protocols, auto_suggestions_allowed, role, status,
       is_deleted, created_at, updated_at
FROM auth_schema.users
WHERE wallet_address = $1
  AND is_deleted = false;

-- name: GetUserByID :one
SELECT id, wallet_address, chain, display_name, risk_tolerance,
       preferred_protocols, auto_suggestions_allowed, role, status,
       is_deleted, created_at, updated_at
FROM auth_schema.users
WHERE id = $1
  AND is_deleted = false;

-- name: CreateUser :one
INSERT INTO auth_schema.users (
    id, wallet_address, chain, auto_suggestions_allowed, role, status,
    is_deleted, created_at, updated_at
)
VALUES ($1, $2, 'solana', true, 'user', 'active', false, $3, $3)
ON CONFLICT (wallet_address) DO NOTHING
RETURNING id, wallet_address, chain, display_name, risk_tolerance,
          preferred_protocols, auto_suggestions_allowed, role, status,
          is_deleted, created_at, updated_at;

-- name: UpdateUserDisplayName :exec
UPDATE auth_schema.users
SET display_name = $2, updated_at = now()
WHERE wallet_address = $1 AND is_deleted = false;

-- name: UpdateUserRiskTolerance :exec
UPDATE auth_schema.users
SET risk_tolerance = $2, updated_at = now()
WHERE wallet_address = $1 AND is_deleted = false;

-- name: UpdateUserPreferredProtocols :exec
UPDATE auth_schema.users
SET preferred_protocols = $2, updated_at = now()
WHERE wallet_address = $1 AND is_deleted = false;

-- name: UpdateUserAutoSuggestions :exec
UPDATE auth_schema.users
SET auto_suggestions_allowed = $2, updated_at = now()
WHERE wallet_address = $1 AND is_deleted = false;

-- name: SoftDeleteUser :exec
UPDATE auth_schema.users
SET is_deleted = true, updated_at = now()
WHERE id = $1 AND is_deleted = false;

-- name: InsertLoginLog :exec
INSERT INTO auth_schema.login_logs (
    id, user_id, wallet_address, ip_address, user_agent,
    country, success, failure_reason, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9);
