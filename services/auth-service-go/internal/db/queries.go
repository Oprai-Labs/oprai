package db

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"
)

// schemaIdentifierRe matches valid PostgreSQL identifiers: starts with a letter
// or underscore, followed by letters, digits, or underscores only.
var schemaIdentifierRe = regexp.MustCompile(`^[a-zA-Z_][a-zA-Z0-9_]*$`)

// Queries provides data access to the auth_schema tables.
type Queries struct {
	pool   *pgxpool.Pool
	schema string
}

// NewQueries creates a new Queries instance.
// Panics if schema is not a valid PostgreSQL identifier — this is a
// programmer/configuration error that must be caught before the service starts.
func NewQueries(pool *pgxpool.Pool, schema string) *Queries {
	if !schemaIdentifierRe.MatchString(schema) {
		panic(fmt.Sprintf("db.NewQueries: invalid schema name %q — must match [a-zA-Z_][a-zA-Z0-9_]*", schema))
	}
	return &Queries{pool: pool, schema: schema}
}

// table returns the fully qualified table name.
func (q *Queries) table(name string) string {
	return fmt.Sprintf("%s.%s", q.schema, name)
}

// userSelectCols is the canonical SELECT column list for auth_schema.users.
// Must match the Scan order in scanUser().
const userSelectCols = `id, wallet_address, chain, display_name, risk_tolerance,
	       preferred_protocols, auto_suggestions_allowed, role, status,
	       is_deleted, status_reason, status_changed_at, status_changed_by,
	       created_at, updated_at`

// scanUser scans a pgx row into a User struct.
func scanUser(row pgx.Row, user *User) error {
	return row.Scan(
		&user.ID,
		&user.WalletAddress,
		&user.Chain,
		&user.DisplayName,
		&user.RiskTolerance,
		&user.PreferredProtocols,
		&user.AutoSuggestionsAllowed,
		&user.Role,
		&user.Status,
		&user.IsDeleted,
		&user.StatusReason,
		&user.StatusChangedAt,
		&user.StatusChangedBy,
		&user.CreatedAt,
		&user.UpdatedAt,
	)
}

const identitySelectCols = `id, account_id, type, chain, identifier, label, is_primary, verified_at, created_at`

// ListIdentitiesByAccount returns every identity linked to an account, primary
// first. Empty (not an error) when the account has none.
func (q *Queries) ListIdentitiesByAccount(ctx context.Context, accountID string) ([]LinkedIdentity, error) {
	query := fmt.Sprintf(`
		SELECT %s
		FROM %s
		WHERE account_id = $1
		ORDER BY is_primary DESC, created_at ASC
	`, identitySelectCols, q.table("linked_identities"))

	rows, err := q.pool.Query(ctx, query, accountID)
	if err != nil {
		return nil, fmt.Errorf("ListIdentitiesByAccount: %w", err)
	}
	defer rows.Close()

	var out []LinkedIdentity
	for rows.Next() {
		var li LinkedIdentity
		if err := rows.Scan(&li.ID, &li.AccountID, &li.Type, &li.Chain,
			&li.Identifier, &li.Label, &li.IsPrimary, &li.VerifiedAt, &li.CreatedAt); err != nil {
			return nil, fmt.Errorf("scan identity: %w", err)
		}
		out = append(out, li)
	}
	return out, rows.Err()
}

// GetUserByWallet retrieves an active (not soft-deleted) user by wallet address.
// Returns nil if not found or deleted.
func (q *Queries) GetUserByWallet(ctx context.Context, walletAddress string) (*User, error) {
	query := fmt.Sprintf(`
		SELECT %s
		FROM %s
		WHERE wallet_address = $1 AND is_deleted = false
	`, userSelectCols, q.table("users"))

	user := &User{}
	if err := scanUser(q.pool.QueryRow(ctx, query, walletAddress), user); err != nil {
		if err == pgx.ErrNoRows {
			return nil, nil
		}
		return nil, fmt.Errorf("GetUserByWallet: %w", err)
	}
	return user, nil
}

// GetOrCreateUser retrieves an existing user or creates a new one.
// This matches the Node.js findOrCreate behavior.
func (q *Queries) GetOrCreateUser(ctx context.Context, walletAddress string) (*User, error) {
	// Try to find existing user first
	user, err := q.GetUserByWallet(ctx, walletAddress)
	if err != nil {
		return nil, err
	}
	if user != nil {
		return user, nil
	}

	// Create new user with INSERT ... ON CONFLICT for safety
	now := time.Now().UTC()
	id := uuid.New().String()

	query := fmt.Sprintf(`
		INSERT INTO %s (id, wallet_address, chain, auto_suggestions_allowed, role, status, is_deleted, created_at, updated_at)
		VALUES ($1, $2, 'solana', true, 'user', 'active', false, $3, $3)
		ON CONFLICT (wallet_address) DO NOTHING
		RETURNING %s
	`, q.table("users"), userSelectCols)

	user = &User{}
	if err = scanUser(q.pool.QueryRow(ctx, query, id, walletAddress, now), user); err != nil {
		if err == pgx.ErrNoRows {
			// ON CONFLICT DO NOTHING returned no row; fetch the existing one
			return q.GetUserByWallet(ctx, walletAddress)
		}
		return nil, fmt.Errorf("GetOrCreateUser insert: %w", err)
	}
	return user, nil
}

// UserUpdate contains the fields that can be updated on a user profile.
type UserUpdate struct {
	DisplayName            *string
	RiskTolerance          *string
	PreferredProtocols     *[]string
	AutoSuggestionsAllowed *bool
}

// UpdateUser updates a user's profile fields. Only non-nil fields are updated.
// Returns the updated user or nil if the wallet was not found.
func (q *Queries) UpdateUser(ctx context.Context, walletAddress string, upd UserUpdate) (*User, error) {
	// Build dynamic SET clause
	setClauses := []string{}
	args := []any{}
	argIdx := 1

	if upd.DisplayName != nil {
		setClauses = append(setClauses, fmt.Sprintf("display_name = $%d", argIdx))
		args = append(args, pgtype.Text{String: *upd.DisplayName, Valid: true})
		argIdx++
	}
	if upd.RiskTolerance != nil {
		setClauses = append(setClauses, fmt.Sprintf("risk_tolerance = $%d", argIdx))
		args = append(args, pgtype.Text{String: *upd.RiskTolerance, Valid: true})
		argIdx++
	}
	if upd.PreferredProtocols != nil {
		setClauses = append(setClauses, fmt.Sprintf("preferred_protocols = $%d", argIdx))
		args = append(args, *upd.PreferredProtocols)
		argIdx++
	}
	if upd.AutoSuggestionsAllowed != nil {
		setClauses = append(setClauses, fmt.Sprintf("auto_suggestions_allowed = $%d", argIdx))
		args = append(args, *upd.AutoSuggestionsAllowed)
		argIdx++
	}

	if len(setClauses) == 0 {
		// Nothing to update; just return the current user
		return q.GetUserByWallet(ctx, walletAddress)
	}

	// Always update updated_at
	setClauses = append(setClauses, fmt.Sprintf("updated_at = $%d", argIdx))
	args = append(args, time.Now().UTC())
	argIdx++

	// WHERE clause
	args = append(args, walletAddress)

	query := fmt.Sprintf(`
		UPDATE %s SET %s
		WHERE wallet_address = $%d AND is_deleted = false
		RETURNING %s
	`, q.table("users"), joinStrings(setClauses, ", "), argIdx, userSelectCols)

	user := &User{}
	if err := scanUser(q.pool.QueryRow(ctx, query, args...), user); err != nil {
		if err == pgx.ErrNoRows {
			return nil, nil
		}
		return nil, fmt.Errorf("UpdateUser: %w", err)
	}
	return user, nil
}

// InsertAuditEvent appends an immutable record to auth_schema.audit_trail.
func (q *Queries) InsertAuditEvent(ctx context.Context, evt *AuditEvent) error {
	severity := evt.Severity
	if severity == "" {
		severity = "info"
	}

	var eventDataJSON []byte
	if len(evt.EventData) > 0 {
		b, err := json.Marshal(evt.EventData)
		if err != nil {
			return fmt.Errorf("InsertAuditEvent: marshal event_data: %w", err)
		}
		eventDataJSON = b
	} else {
		eventDataJSON = []byte("{}")
	}

	var userID *string
	if evt.UserID != "" {
		userID = &evt.UserID
	}

	query := fmt.Sprintf(`
		INSERT INTO %s (
			event_type, severity, entity_type, entity_id,
			user_id, wallet_address, session_id,
			event_data, ip_address, user_agent,
			request_method, request_path, request_id,
			response_status, response_time_ms,
			transaction_signature, transaction_amount, transaction_token
		) VALUES (
			$1, $2, $3, $4,
			$5, $6, $7,
			$8::jsonb, $9, $10,
			$11, $12, $13,
			$14, $15,
			$16, $17, $18
		)
	`, q.table("audit_trail"))

	_, err := q.pool.Exec(ctx, query,
		evt.EventType, severity, nilIfEmpty(evt.EntityType), nilIfEmpty(evt.EntityID),
		userID, nilIfEmpty(evt.WalletAddress), nilIfEmpty(evt.SessionID),
		eventDataJSON, nilIfEmpty(evt.IPAddress), nilIfEmpty(evt.UserAgent),
		nilIfEmpty(evt.RequestMethod), nilIfEmpty(evt.RequestPath), nilIfEmpty(evt.RequestID),
		nilIfInt(evt.ResponseStatus), nilIfZero(evt.ResponseTimeMs),
		nilIfEmpty(evt.TransactionSignature), nilIfZero(evt.TransactionAmount), nilIfEmpty(evt.TransactionToken),
	)
	if err != nil {
		return fmt.Errorf("InsertAuditEvent: %w", err)
	}
	return nil
}

func nilIfEmpty(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func nilIfInt(i int) *int {
	if i == 0 {
		return nil
	}
	return &i
}

func nilIfZero(f float64) *float64 {
	if f == 0 {
		return nil
	}
	return &f
}

// InsertLoginLog records a login attempt in the login_logs table.
func (q *Queries) InsertLoginLog(ctx context.Context, log *LoginLog) error {
	if log.ID == "" {
		log.ID = uuid.New().String()
	}
	if log.CreatedAt.IsZero() {
		log.CreatedAt = time.Now().UTC()
	}

	query := fmt.Sprintf(`
		INSERT INTO %s (id, user_id, wallet_address, ip_address, user_agent, country, success, failure_reason, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
	`, q.table("login_logs"))

	_, err := q.pool.Exec(ctx, query,
		log.ID,
		log.UserID,
		log.WalletAddress,
		log.IPAddress,
		log.UserAgent,
		log.Country,
		log.Success,
		log.FailureReason,
		log.CreatedAt,
	)
	if err != nil {
		return fmt.Errorf("InsertLoginLog: %w", err)
	}
	return nil
}

// GetSpendingLimits retrieves spending limits for a wallet. Returns nil if not set.
func (q *Queries) GetSpendingLimits(ctx context.Context, walletAddress string) (*SpendingLimits, error) {
	query := fmt.Sprintf(`
		SELECT id, user_id, wallet_address, max_per_tx_usd, max_per_day_usd, created_at, updated_at
		FROM %s
		WHERE wallet_address = $1
		LIMIT 1
	`, q.table("spending_limits"))

	row := q.pool.QueryRow(ctx, query, walletAddress)
	sl := &SpendingLimits{}
	err := row.Scan(&sl.ID, &sl.UserID, &sl.WalletAddress, &sl.MaxPerTxUsd, &sl.MaxPerDayUsd, &sl.CreatedAt, &sl.UpdatedAt)
	if err != nil {
		if err.Error() == "no rows in result set" {
			return nil, nil // no limits set — treat as unlimited
		}
		return nil, fmt.Errorf("GetSpendingLimits: %w", err)
	}
	return sl, nil
}

// UpsertSpendingLimits creates or updates spending limits for a user.
func (q *Queries) UpsertSpendingLimits(ctx context.Context, userID, walletAddress string, maxPerTxUsd, maxPerDayUsd float64) (*SpendingLimits, error) {
	query := fmt.Sprintf(`
		INSERT INTO %s (user_id, wallet_address, max_per_tx_usd, max_per_day_usd)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT (user_id) DO UPDATE
		  SET max_per_tx_usd  = EXCLUDED.max_per_tx_usd,
		      max_per_day_usd = EXCLUDED.max_per_day_usd,
		      updated_at      = now()
		RETURNING id, user_id, wallet_address, max_per_tx_usd, max_per_day_usd, created_at, updated_at
	`, q.table("spending_limits"))

	row := q.pool.QueryRow(ctx, query, userID, walletAddress, maxPerTxUsd, maxPerDayUsd)
	sl := &SpendingLimits{}
	err := row.Scan(&sl.ID, &sl.UserID, &sl.WalletAddress, &sl.MaxPerTxUsd, &sl.MaxPerDayUsd, &sl.CreatedAt, &sl.UpdatedAt)
	if err != nil {
		return nil, fmt.Errorf("UpsertSpendingLimits: %w", err)
	}
	return sl, nil
}

// GetTodaySpendingTotal returns the wallet's accumulated spend (USD) for the
// current UTC date. Returns 0 if no entry exists yet (read-only path used by
// /internal/spending/check before the user has signed anything today).
func (q *Queries) GetTodaySpendingTotal(ctx context.Context, walletAddress string) (float64, error) {
	query := fmt.Sprintf(`
		SELECT COALESCE(total_usd, 0) FROM %s
		WHERE wallet_address = $1 AND date_key = (now() AT TIME ZONE 'UTC')::date
	`, q.table("spending_daily"))
	var total float64
	err := q.pool.QueryRow(ctx, query, walletAddress).Scan(&total)
	if err != nil {
		if err.Error() == "no rows in result set" {
			return 0, nil
		}
		return 0, fmt.Errorf("GetTodaySpendingTotal: %w", err)
	}
	return total, nil
}

// IncrementTodaySpending atomically adds amountUsd to the wallet's daily total
// and returns the new total. Atomic at the row level (single UPSERT), so two
// concurrent /commit calls cannot interleave to skip the cap.
func (q *Queries) IncrementTodaySpending(ctx context.Context, walletAddress string, amountUsd float64) (float64, error) {
	if amountUsd < 0 {
		return 0, fmt.Errorf("IncrementTodaySpending: negative amount")
	}
	query := fmt.Sprintf(`
		INSERT INTO %s (wallet_address, date_key, total_usd)
		VALUES ($1, (now() AT TIME ZONE 'UTC')::date, $2)
		ON CONFLICT (wallet_address, date_key) DO UPDATE
		  SET total_usd  = %s.total_usd + EXCLUDED.total_usd,
		      updated_at = now()
		RETURNING total_usd
	`, q.table("spending_daily"), q.table("spending_daily"))
	var total float64
	err := q.pool.QueryRow(ctx, query, walletAddress, amountUsd).Scan(&total)
	if err != nil {
		return 0, fmt.Errorf("IncrementTodaySpending: %w", err)
	}
	return total, nil
}

// joinStrings joins a slice of strings with the given separator.
func joinStrings(strs []string, sep string) string {
	if len(strs) == 0 {
		return ""
	}
	result := strs[0]
	for _, s := range strs[1:] {
		result += sep + s
	}
	return result
}
