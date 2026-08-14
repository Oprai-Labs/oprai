package db

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Queries provides all cross-schema raw SQL queries for the admin service.
type Queries struct {
	pool *pgxpool.Pool
}

// NewQueries creates a new Queries instance backed by the given connection pool.
func NewQueries(pool *pgxpool.Pool) *Queries {
	return &Queries{pool: pool}
}

// Ping checks that the database connection pool is healthy.
func (q *Queries) Ping(ctx context.Context) error {
	return q.pool.Ping(ctx)
}

// --------------------------------------------------------------------------
// Admin user queries
// --------------------------------------------------------------------------

// adminUserSelectCols is the canonical SELECT column list for admin_schema.admin_users.
const adminUserSelectCols = `id, username, password_hash, role, must_change_password, last_login_at, created_at, updated_at`

// scanAdminUser scans a pgx row into an AdminUser struct.
func scanAdminUser(row pgx.Row, u *AdminUser) error {
	return row.Scan(
		&u.ID, &u.Username, &u.PasswordHash, &u.Role,
		&u.MustChangePassword, &u.LastLoginAt,
		&u.CreatedAt, &u.UpdatedAt,
	)
}

// GetAdminByUsername retrieves an admin user by username.
func (q *Queries) GetAdminByUsername(ctx context.Context, username string) (*AdminUser, error) {
	row := q.pool.QueryRow(ctx,
		`SELECT `+adminUserSelectCols+`
		 FROM admin_schema.admin_users WHERE username = $1`, username)

	var u AdminUser
	if err := scanAdminUser(row, &u); err != nil {
		return nil, err
	}
	return &u, nil
}

// GetAdminByID retrieves an admin user by ID.
func (q *Queries) GetAdminByID(ctx context.Context, id string) (*AdminUser, error) {
	row := q.pool.QueryRow(ctx,
		`SELECT `+adminUserSelectCols+`
		 FROM admin_schema.admin_users WHERE id = $1`, id)

	var u AdminUser
	if err := scanAdminUser(row, &u); err != nil {
		return nil, err
	}
	return &u, nil
}

// GetAdminIDByUsername retrieves just the admin ID for a given username.
func (q *Queries) GetAdminIDByUsername(ctx context.Context, username string) (string, error) {
	var id string
	err := q.pool.QueryRow(ctx,
		`SELECT id FROM admin_schema.admin_users WHERE username = $1`, username).Scan(&id)
	return id, err
}

// ListAdminUsers returns all admin users (without password hashes).
func (q *Queries) ListAdminUsers(ctx context.Context) ([]AdminUserPublic, error) {
	rows, err := q.pool.Query(ctx,
		`SELECT id, username, role, must_change_password, last_login_at, created_at
		 FROM admin_schema.admin_users ORDER BY created_at ASC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var admins []AdminUserPublic
	for rows.Next() {
		var a AdminUserPublic
		if err := rows.Scan(&a.ID, &a.Username, &a.Role, &a.MustChangePassword, &a.LastLoginAt, &a.CreatedAt); err != nil {
			return nil, err
		}
		admins = append(admins, a)
	}
	return admins, rows.Err()
}

// CreateAdminUser creates a new admin user and returns it.
// New accounts always require a password change on first login.
func (q *Queries) CreateAdminUser(ctx context.Context, username, passwordHash string) (*AdminUser, error) {
	row := q.pool.QueryRow(ctx,
		`INSERT INTO admin_schema.admin_users
		     (id, username, password_hash, role, must_change_password, created_at, updated_at)
		 VALUES (gen_random_uuid(), $1, $2, 'superadmin', true, NOW(), NOW())
		 RETURNING `+adminUserSelectCols,
		username, passwordHash)

	var u AdminUser
	if err := scanAdminUser(row, &u); err != nil {
		return nil, err
	}
	return &u, nil
}

// DeleteAdminUser deletes an admin user by ID.
func (q *Queries) DeleteAdminUser(ctx context.Context, id string) error {
	_, err := q.pool.Exec(ctx,
		`DELETE FROM admin_schema.admin_users WHERE id = $1`, id)
	return err
}

// UpdateAdminPassword updates the password hash for an admin user.
// When an admin resets another user's password, must_change_password is set to true
// so they are forced to change it on next login.
func (q *Queries) UpdateAdminPassword(ctx context.Context, id, passwordHash string, forceChange bool) error {
	_, err := q.pool.Exec(ctx,
		`UPDATE admin_schema.admin_users
		 SET password_hash = $1, must_change_password = $2, updated_at = NOW()
		 WHERE id = $3`,
		passwordHash, forceChange, id)
	return err
}

// UpdateLastLogin records the time of the most recent successful login.
func (q *Queries) UpdateLastLogin(ctx context.Context, id string) error {
	_, err := q.pool.Exec(ctx,
		`UPDATE admin_schema.admin_users SET last_login_at = NOW() WHERE id = $1`,
		id)
	return err
}

// --------------------------------------------------------------------------
// Audit log queries
// --------------------------------------------------------------------------

// CreateAuditLog inserts a new audit log entry.
func (q *Queries) CreateAuditLog(ctx context.Context, entry AuditLogEntry) error {
	var detailsJSON *string
	if entry.Details != nil {
		b, err := json.Marshal(entry.Details)
		if err == nil {
			s := string(b)
			detailsJSON = &s
		}
	}

	_, err := q.pool.Exec(ctx,
		`INSERT INTO admin_schema.admin_audit_log
		 (id, admin_id, admin_username, action, target_type, target_id, details, ip_address, created_at)
		 VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6::jsonb, $7, NOW())`,
		entry.AdminID,
		entry.AdminUsername,
		entry.Action,
		nilIfEmpty(entry.TargetType),
		nilIfEmpty(entry.TargetID),
		detailsJSON,
		nilIfEmpty(entry.IPAddress),
	)
	return err
}

// GetAuditLogs returns a paginated list of audit log entries.
func (q *Queries) GetAuditLogs(ctx context.Context, params AuditLogListParams) (*PaginatedResult, error) {
	offset := (params.Page - 1) * params.Limit

	var conditions []string
	var args []interface{}
	argIdx := 1

	if params.Admin != "" {
		conditions = append(conditions, fmt.Sprintf("a.admin_username = $%d", argIdx))
		args = append(args, params.Admin)
		argIdx++
	}
	if params.Action != "" {
		conditions = append(conditions, fmt.Sprintf("a.action = $%d", argIdx))
		args = append(args, params.Action)
		argIdx++
	}
	if params.TargetType != "" {
		conditions = append(conditions, fmt.Sprintf("a.target_type = $%d", argIdx))
		args = append(args, params.TargetType)
		argIdx++
	}
	if params.Search != "" {
		conditions = append(conditions, fmt.Sprintf(
			"(a.admin_username ILIKE $%d OR a.target_id ILIKE $%d OR a.action ILIKE $%d)", argIdx, argIdx, argIdx))
		args = append(args, "%"+params.Search+"%")
		argIdx++
	}

	argIdx = addDateFilters(&conditions, &args, argIdx, "a.created_at", params.From, params.To)

	where := ""
	if len(conditions) > 0 {
		where = "WHERE " + strings.Join(conditions, " AND ")
	}

	// Count
	total, err := q.queryCount(ctx,
		fmt.Sprintf(`SELECT COUNT(*) FROM admin_schema.admin_audit_log a %s`, where), args...)
	if err != nil {
		return nil, err
	}

	// Data
	dataArgs := append(args, params.Limit, offset)
	rows, err := q.pool.Query(ctx,
		fmt.Sprintf(`SELECT a.* FROM admin_schema.admin_audit_log a %s
		 ORDER BY a.created_at DESC LIMIT $%d OFFSET $%d`, where, argIdx, argIdx+1),
		dataArgs...)
	if err != nil {
		return nil, err
	}

	data, err := rowsToMaps(rows)
	if err != nil {
		return nil, err
	}

	return &PaginatedResult{
		Data:       data,
		Total:      total,
		Page:       params.Page,
		Limit:      params.Limit,
		TotalPages: int(math.Ceil(float64(total) / float64(params.Limit))),
	}, nil
}

// GetAuditLogsForTarget returns the most recent audit logs for a specific target.
// Hard-capped at 500 rows to prevent unbounded result sets.
func (q *Queries) GetAuditLogsForTarget(ctx context.Context, targetType, targetID string) ([]map[string]interface{}, error) {
	rows, err := q.pool.Query(ctx,
		`SELECT * FROM admin_schema.admin_audit_log
		 WHERE target_type = $1 AND target_id = $2
		 ORDER BY created_at DESC
		 LIMIT 500`,
		targetType, targetID)
	if err != nil {
		return nil, err
	}
	return rowsToMaps(rows)
}

// --------------------------------------------------------------------------
// Dashboard queries
// --------------------------------------------------------------------------

// GetDashboardStats returns aggregated statistics from all schemas.
// All counters are fetched in a single round-trip to avoid N+1 latency.
func (q *Queries) GetDashboardStats(ctx context.Context) (*DashboardStats, error) {
	stats := &DashboardStats{}

	// ── Single query for all scalar counters ──────────────────────────────────
	const countersSQL = `
		SELECT
			(SELECT COUNT(*) FROM auth_schema.users WHERE is_deleted = false)                            AS total_users,
			(SELECT COUNT(*) FROM auth_schema.users WHERE created_at >= CURRENT_DATE AND is_deleted = false) AS users_today,
			(SELECT COUNT(*) FROM auth_schema.users WHERE created_at < NOW() - INTERVAL '7 days' AND is_deleted = false) AS users_7d_ago,

			(SELECT COUNT(*) FROM chat_schema.chat_sessions WHERE is_deleted = false)                    AS total_sessions,
			(SELECT COUNT(*) FROM chat_schema.chat_sessions WHERE is_deleted = false AND updated_at >= NOW() - INTERVAL '24 hours') AS sessions_active,
			(SELECT COUNT(*) FROM chat_schema.chat_sessions WHERE created_at < NOW() - INTERVAL '7 days' AND is_deleted = false) AS sessions_7d_ago,

			(SELECT COUNT(*) FROM solana_schema.transactions)                                            AS total_tx,
			(SELECT COUNT(*) FROM solana_schema.transactions WHERE created_at >= CURRENT_DATE)           AS tx_today,
			(SELECT COUNT(*) FROM solana_schema.transactions WHERE created_at < NOW() - INTERVAL '7 days') AS tx_7d_ago,

			(SELECT COUNT(*) FROM chat_schema.chat_messages)                                             AS total_messages,
			(SELECT COUNT(*) FROM chat_schema.chat_messages WHERE created_at >= CURRENT_DATE)            AS messages_today,
			(SELECT COUNT(*) FROM chat_schema.chat_messages WHERE created_at < NOW() - INTERVAL '7 days') AS messages_7d_ago`

	row := q.pool.QueryRow(ctx, countersSQL)
	err := row.Scan(
		&stats.TotalUsers, &stats.UsersToday, &stats.Trends.Users7dAgo,
		&stats.TotalSessions, &stats.SessionsActive, &stats.Trends.Sessions7dAgo,
		&stats.TotalTransactions, &stats.TxToday, &stats.Trends.Tx7dAgo,
		&stats.TotalMessages, &stats.MessagesToday, &stats.Trends.Messages7dAgo,
	)
	if err != nil {
		return nil, fmt.Errorf("dashboard counters: %w", err)
	}

	// ── TX breakdown by status ────────────────────────────────────────────────
	statusRows, err := q.pool.Query(ctx,
		`SELECT status::text, COUNT(*)::int AS count
		 FROM solana_schema.transactions
		 GROUP BY status
		 ORDER BY count DESC`)
	if err == nil {
		defer statusRows.Close()
		for statusRows.Next() {
			var sc StatusCount
			if err := statusRows.Scan(&sc.Status, &sc.Count); err == nil {
				stats.TxByStatus = append(stats.TxByStatus, sc)
			}
		}
	}
	if stats.TxByStatus == nil {
		stats.TxByStatus = []StatusCount{}
	}

	// ── TX breakdown by action ────────────────────────────────────────────────
	actionRows, err := q.pool.Query(ctx,
		`SELECT action::text, COUNT(*)::int AS count
		 FROM solana_schema.transactions
		 GROUP BY action
		 ORDER BY count DESC`)
	if err == nil {
		defer actionRows.Close()
		for actionRows.Next() {
			var ac ActionCount
			if err := actionRows.Scan(&ac.Action, &ac.Count); err == nil {
				stats.TxByAction = append(stats.TxByAction, ac)
			}
		}
	}
	if stats.TxByAction == nil {
		stats.TxByAction = []ActionCount{}
	}

	// ── Recent users (10 rows) ────────────────────────────────────────────────
	recentUserRows, err := q.pool.Query(ctx,
		`SELECT id, wallet_address, display_name, role, status, created_at
		 FROM auth_schema.users
		 WHERE is_deleted = false
		 ORDER BY created_at DESC
		 LIMIT 10`)
	if err == nil {
		defer recentUserRows.Close()
		for recentUserRows.Next() {
			var u RecentUser
			if err := recentUserRows.Scan(&u.ID, &u.WalletAddress, &u.DisplayName, &u.Role, &u.Status, &u.CreatedAt); err == nil {
				stats.RecentUsers = append(stats.RecentUsers, u)
			}
		}
	}
	if stats.RecentUsers == nil {
		stats.RecentUsers = []RecentUser{}
	}

	// ── Recent transactions (10 rows) ────────────────────────────────────────
	recentTxRows, err := q.pool.Query(ctx,
		`SELECT t.id, t.user_id, u.wallet_address AS user_wallet,
		        t.action::text, t.protocol, t.status::text, t.tx_hash, t.created_at
		 FROM solana_schema.transactions t
		 LEFT JOIN auth_schema.users u ON u.id = t.user_id
		 ORDER BY t.created_at DESC
		 LIMIT 10`)
	if err == nil {
		defer recentTxRows.Close()
		for recentTxRows.Next() {
			var tx RecentTransaction
			if err := recentTxRows.Scan(&tx.ID, &tx.UserID, &tx.UserWallet, &tx.Action, &tx.Protocol, &tx.Status, &tx.TxHash, &tx.CreatedAt); err == nil {
				stats.RecentTx = append(stats.RecentTx, tx)
			}
		}
	}
	if stats.RecentTx == nil {
		stats.RecentTx = []RecentTransaction{}
	}

	return stats, nil
}

// --------------------------------------------------------------------------
// User queries (cross-schema)
// --------------------------------------------------------------------------

// GetUsers returns a paginated list of users.
func (q *Queries) GetUsers(ctx context.Context, params UserListParams) (*PaginatedResult, error) {
	offset := (params.Page - 1) * params.Limit

	allowedSorts := map[string]bool{
		"created_at": true, "wallet_address": true, "display_name": true, "role": true, "status": true,
	}
	sortCol := "created_at"
	if allowedSorts[params.Sort] {
		sortCol = params.Sort
	}
	sortDir := "DESC"
	if strings.ToUpper(params.Order) == "ASC" {
		sortDir = "ASC"
	}

	var conditions []string
	var args []interface{}
	argIdx := 1

	if params.Search != "" {
		conditions = append(conditions, fmt.Sprintf(
			"(u.wallet_address ILIKE $%d OR u.display_name ILIKE $%d)", argIdx, argIdx))
		args = append(args, "%"+params.Search+"%")
		argIdx++
	}
	if params.Status != "" {
		conditions = append(conditions, fmt.Sprintf("u.status = $%d", argIdx))
		args = append(args, params.Status)
		argIdx++
	}
	if params.Role != "" {
		conditions = append(conditions, fmt.Sprintf("u.role = $%d", argIdx))
		args = append(args, params.Role)
		argIdx++
	}
	argIdx = addDateFilters(&conditions, &args, argIdx, "u.created_at", params.From, params.To)

	where := ""
	if len(conditions) > 0 {
		where = "WHERE " + strings.Join(conditions, " AND ")
	}

	total, err := q.queryCount(ctx,
		fmt.Sprintf(`SELECT COUNT(*) FROM auth_schema.users u %s`, where), args...)
	if err != nil {
		return nil, err
	}

	dataArgs := append(args, params.Limit, offset)
	rows, err := q.pool.Query(ctx,
		fmt.Sprintf(`SELECT u.id, u.wallet_address, u.display_name, u.role, u.status, u.created_at,
		   (SELECT COUNT(*) FROM chat_schema.chat_sessions s WHERE s.user_id = u.id) as session_count,
		   (SELECT COUNT(*) FROM solana_schema.transactions t WHERE t.user_id = u.id) as tx_count
		 FROM auth_schema.users u %s
		 ORDER BY u.%s %s
		 LIMIT $%d OFFSET $%d`, where, sortCol, sortDir, argIdx, argIdx+1),
		dataArgs...)
	if err != nil {
		return nil, err
	}

	data, err := rowsToMaps(rows)
	if err != nil {
		return nil, err
	}

	return &PaginatedResult{
		Data:       data,
		Total:      total,
		Page:       params.Page,
		Limit:      params.Limit,
		TotalPages: int(math.Ceil(float64(total) / float64(params.Limit))),
	}, nil
}

// GetUserByID retrieves a single user with cross-schema activity counts.
func (q *Queries) GetUserByID(ctx context.Context, id string) (*UserDetail, error) {
	var u UserDetail
	err := q.pool.QueryRow(ctx,
		`SELECT u.id, u.wallet_address, u.display_name, u.email, u.status, u.role,
		        u.created_at, u.updated_at, u.is_deleted,
		        (SELECT COUNT(*) FROM chat_schema.chat_sessions s WHERE s.user_id = u.id)::int,
		        (SELECT COUNT(*) FROM solana_schema.transactions t WHERE t.user_id = u.id)::int,
		        (SELECT COUNT(*) FROM chat_schema.chat_messages m
		         WHERE m.session_id IN (SELECT s2.id FROM chat_schema.chat_sessions s2 WHERE s2.user_id = u.id))::int
		 FROM auth_schema.users u WHERE u.id = $1`, id).
		Scan(&u.ID, &u.WalletAddress, &u.DisplayName, &u.Email, &u.Status, &u.Role,
			&u.CreatedAt, &u.UpdatedAt, &u.IsDeleted,
			&u.SessionCount, &u.TxCount, &u.MessageCount)
	if err != nil {
		return nil, fmt.Errorf("user not found")
	}
	return &u, nil
}

// GetUserOverview retrieves a user with recent sessions and transactions.
func (q *Queries) GetUserOverview(ctx context.Context, id string) (*UserOverview, error) {
	user, err := q.GetUserByID(ctx, id)
	if err != nil {
		return nil, err
	}

	overview := &UserOverview{
		UserDetail:     *user,
		RecentSessions: []RecentSession{},
		RecentTx:       []RecentTransaction{},
		RecentIPs:      []string{},
	}

	// Recent sessions
	sessRows, err := q.pool.Query(ctx,
		`SELECT s.id, s.title, s.message_count, s.is_deleted, s.pinned, s.created_at, s.updated_at
		 FROM chat_schema.chat_sessions s WHERE s.user_id = $1 AND s.is_deleted = false
		 ORDER BY s.created_at DESC LIMIT 5`, id)
	if err == nil {
		defer sessRows.Close()
		for sessRows.Next() {
			var s RecentSession
			if err := sessRows.Scan(&s.ID, &s.Title, &s.MessageCount, &s.IsDeleted, &s.Pinned, &s.CreatedAt, &s.UpdatedAt); err == nil {
				overview.RecentSessions = append(overview.RecentSessions, s)
			}
		}
	}

	// Recent transactions
	txRows, err := q.pool.Query(ctx,
		`SELECT id, user_id, NULL::text, action::text, protocol, status::text, tx_hash, created_at
		 FROM solana_schema.transactions WHERE user_id = $1
		 ORDER BY created_at DESC LIMIT 5`, id)
	if err == nil {
		defer txRows.Close()
		for txRows.Next() {
			var tx RecentTransaction
			if err := txRows.Scan(&tx.ID, &tx.UserID, &tx.UserWallet, &tx.Action, &tx.Protocol, &tx.Status, &tx.TxHash, &tx.CreatedAt); err == nil {
				overview.RecentTx = append(overview.RecentTx, tx)
			}
		}
	}

	return overview, nil
}

// GetUserSessions returns paginated sessions for a user.
func (q *Queries) GetUserSessions(ctx context.Context, userID string, params DateRangeParams) (*PaginatedResult, error) {
	offset := (params.Page - 1) * params.Limit

	conditions := []string{"s.user_id = $1"}
	args := []interface{}{userID}
	argIdx := 2

	argIdx = addDateFilters(&conditions, &args, argIdx, "s.created_at", params.From, params.To)

	where := "WHERE " + strings.Join(conditions, " AND ")

	total, err := q.queryCount(ctx,
		fmt.Sprintf(`SELECT COUNT(*) FROM chat_schema.chat_sessions s %s`, where), args...)
	if err != nil {
		return nil, err
	}

	dataArgs := append(args, params.Limit, offset)
	rows, err := q.pool.Query(ctx,
		fmt.Sprintf(`SELECT s.id, s.user_id, s.wallet_address, s.title, s.message_count,
		 s.is_deleted, s.pinned, s.created_at, s.updated_at
		 FROM chat_schema.chat_sessions s %s
		 ORDER BY s.created_at DESC LIMIT $%d OFFSET $%d`, where, argIdx, argIdx+1),
		dataArgs...)
	if err != nil {
		return nil, err
	}

	data, err := rowsToMaps(rows)
	if err != nil {
		return nil, err
	}

	return &PaginatedResult{
		Data:       data,
		Total:      total,
		Page:       params.Page,
		Limit:      params.Limit,
		TotalPages: int(math.Ceil(float64(total) / float64(params.Limit))),
	}, nil
}

// GetUserTransactions returns paginated transactions for a user.
func (q *Queries) GetUserTransactions(ctx context.Context, userID string, params DateRangeParams) (*PaginatedResult, error) {
	offset := (params.Page - 1) * params.Limit

	conditions := []string{"t.user_id = $1"}
	args := []interface{}{userID}
	argIdx := 2

	argIdx = addDateFilters(&conditions, &args, argIdx, "t.created_at", params.From, params.To)

	where := "WHERE " + strings.Join(conditions, " AND ")

	total, err := q.queryCount(ctx,
		fmt.Sprintf(`SELECT COUNT(*) FROM solana_schema.transactions t %s`, where), args...)
	if err != nil {
		return nil, err
	}

	dataArgs := append(args, params.Limit, offset)
	rows, err := q.pool.Query(ctx,
		fmt.Sprintf(`SELECT t.*, t.action::text as action, t.status::text as status
		 FROM solana_schema.transactions t %s
		 ORDER BY created_at DESC LIMIT $%d OFFSET $%d`, where, argIdx, argIdx+1),
		dataArgs...)
	if err != nil {
		return nil, err
	}

	data, err := rowsToMaps(rows)
	if err != nil {
		return nil, err
	}

	return &PaginatedResult{
		Data:       data,
		Total:      total,
		Page:       params.Page,
		Limit:      params.Limit,
		TotalPages: int(math.Ceil(float64(total) / float64(params.Limit))),
	}, nil
}

// UpdateUserStatus updates a user's status and records who made the change.
func (q *Queries) UpdateUserStatus(ctx context.Context, userID, status, reason, adminUsername string) (*UserDetail, error) {
	_, err := q.pool.Exec(ctx,
		`UPDATE auth_schema.users
		 SET status = $1, status_reason = $2, status_changed_at = NOW(), status_changed_by = $3
		 WHERE id = $4`,
		status, reason, adminUsername, userID)
	if err != nil {
		return nil, err
	}
	return q.GetUserByID(ctx, userID)
}

// UpdateUserRole updates a user's role.
func (q *Queries) UpdateUserRole(ctx context.Context, userID, role string) (*UserDetail, error) {
	_, err := q.pool.Exec(ctx,
		`UPDATE auth_schema.users SET role = $1 WHERE id = $2`,
		role, userID)
	if err != nil {
		return nil, err
	}
	return q.GetUserByID(ctx, userID)
}

// --------------------------------------------------------------------------
// Transaction queries
// --------------------------------------------------------------------------

// GetTransactions returns a paginated list of transactions.
func (q *Queries) GetTransactions(ctx context.Context, params TransactionListParams) (*PaginatedResult, error) {
	offset := (params.Page - 1) * params.Limit

	var conditions []string
	var args []interface{}
	argIdx := 1

	if params.Status != "" {
		conditions = append(conditions, fmt.Sprintf("t.status = $%d", argIdx))
		args = append(args, params.Status)
		argIdx++
	}
	if params.Action != "" {
		conditions = append(conditions, fmt.Sprintf("t.action = $%d", argIdx))
		args = append(args, params.Action)
		argIdx++
	}
	if params.Protocol != "" {
		conditions = append(conditions, fmt.Sprintf("t.protocol = $%d", argIdx))
		args = append(args, params.Protocol)
		argIdx++
	}
	if params.Search != "" {
		conditions = append(conditions, fmt.Sprintf(
			"(t.tx_hash ILIKE $%d OR u.wallet_address ILIKE $%d)", argIdx, argIdx))
		args = append(args, "%"+params.Search+"%")
		argIdx++
	}
	argIdx = addDateFilters(&conditions, &args, argIdx, "t.created_at", params.From, params.To)

	where := ""
	if len(conditions) > 0 {
		where = "WHERE " + strings.Join(conditions, " AND ")
	}

	total, err := q.queryCount(ctx,
		fmt.Sprintf(`SELECT COUNT(*) FROM solana_schema.transactions t LEFT JOIN auth_schema.users u ON u.id = t.user_id %s`, where),
		args...)
	if err != nil {
		return nil, err
	}

	dataArgs := append(args, params.Limit, offset)
	rows, err := q.pool.Query(ctx,
		fmt.Sprintf(`SELECT t.*, t.action::text as action, t.status::text as status,
		   u.wallet_address as user_wallet, u.display_name
		 FROM solana_schema.transactions t
		 LEFT JOIN auth_schema.users u ON u.id = t.user_id
		 %s ORDER BY t.created_at DESC LIMIT $%d OFFSET $%d`, where, argIdx, argIdx+1),
		dataArgs...)
	if err != nil {
		return nil, err
	}

	data, err := rowsToMaps(rows)
	if err != nil {
		return nil, err
	}

	return &PaginatedResult{
		Data:       data,
		Total:      total,
		Page:       params.Page,
		Limit:      params.Limit,
		TotalPages: int(math.Ceil(float64(total) / float64(params.Limit))),
	}, nil
}

// GetTransactionByID retrieves a single transaction with user info.
func (q *Queries) GetTransactionByID(ctx context.Context, id string) (*TransactionDetail, error) {
	var tx TransactionDetail
	err := q.pool.QueryRow(ctx,
		`SELECT t.id, t.user_id, u.wallet_address, u.display_name,
		        t.action::text, t.protocol, t.status::text, t.tx_hash, t.amount::text,
		        t.created_at, t.updated_at
		 FROM solana_schema.transactions t
		 LEFT JOIN auth_schema.users u ON u.id = t.user_id
		 WHERE t.id = $1`, id).
		Scan(&tx.ID, &tx.UserID, &tx.UserWallet, &tx.DisplayName,
			&tx.Action, &tx.Protocol, &tx.Status, &tx.TxHash, &tx.Amount,
			&tx.CreatedAt, &tx.UpdatedAt)
	if err != nil {
		return nil, fmt.Errorf("transaction not found")
	}
	return &tx, nil
}

// --------------------------------------------------------------------------
// Session queries
// --------------------------------------------------------------------------

// GetSessions returns a paginated list of chat sessions.
func (q *Queries) GetSessions(ctx context.Context, params SessionListParams) (*PaginatedResult, error) {
	offset := (params.Page - 1) * params.Limit

	var conditions []string
	var args []interface{}
	argIdx := 1

	if params.Search != "" {
		conditions = append(conditions, fmt.Sprintf(
			"(s.wallet_address ILIKE $%d OR s.title ILIKE $%d)", argIdx, argIdx))
		args = append(args, "%"+params.Search+"%")
		argIdx++
	}
	argIdx = addDateFilters(&conditions, &args, argIdx, "s.created_at", params.From, params.To)

	where := ""
	if len(conditions) > 0 {
		where = "WHERE " + strings.Join(conditions, " AND ")
	}

	total, err := q.queryCount(ctx,
		fmt.Sprintf(`SELECT COUNT(*) FROM chat_schema.chat_sessions s %s`, where), args...)
	if err != nil {
		return nil, err
	}

	dataArgs := append(args, params.Limit, offset)
	rows, err := q.pool.Query(ctx,
		fmt.Sprintf(`SELECT s.id, s.user_id, s.wallet_address, s.title, s.message_count,
		 s.is_deleted, s.pinned, s.created_at, s.updated_at, u.display_name
		 FROM chat_schema.chat_sessions s
		 LEFT JOIN auth_schema.users u ON u.id = s.user_id
		 %s ORDER BY s.created_at DESC LIMIT $%d OFFSET $%d`, where, argIdx, argIdx+1),
		dataArgs...)
	if err != nil {
		return nil, err
	}

	data, err := rowsToMaps(rows)
	if err != nil {
		return nil, err
	}

	return &PaginatedResult{
		Data:       data,
		Total:      total,
		Page:       params.Page,
		Limit:      params.Limit,
		TotalPages: int(math.Ceil(float64(total) / float64(params.Limit))),
	}, nil
}

// GetSessionMessages returns all messages for a session.
func (q *Queries) GetSessionMessages(ctx context.Context, sessionID string) ([]map[string]interface{}, error) {
	rows, err := q.pool.Query(ctx,
		`SELECT id, session_id, wallet_address, role, content, metadata, created_at, updated_at
		 FROM chat_schema.chat_messages
		 WHERE session_id = $1
		 ORDER BY created_at ASC`, sessionID)
	if err != nil {
		return nil, err
	}
	return rowsToMaps(rows)
}

// CloseSession soft-deletes a chat session.
func (q *Queries) CloseSession(ctx context.Context, sessionID string) error {
	_, err := q.pool.Exec(ctx,
		`UPDATE chat_schema.chat_sessions SET is_deleted = true, deleted_at = NOW(), updated_at = NOW() WHERE id = $1`,
		sessionID)
	return err
}

// --------------------------------------------------------------------------
// Export queries
// --------------------------------------------------------------------------

// ExportUsers exports all users with counts.
func (q *Queries) ExportUsers(ctx context.Context, params DateRangeParams) ([]map[string]interface{}, error) {
	var conditions []string
	var args []interface{}
	argIdx := 1

	argIdx = addDateFilters(&conditions, &args, argIdx, "u.created_at", params.From, params.To)
	_ = argIdx

	where := ""
	if len(conditions) > 0 {
		where = "WHERE " + strings.Join(conditions, " AND ")
	}

	rows, err := q.pool.Query(ctx,
		fmt.Sprintf(`SELECT u.id, u.wallet_address, u.display_name, u.role, u.status, u.status_reason, u.created_at,
		   (SELECT COUNT(*) FROM chat_schema.chat_sessions s WHERE s.user_id = u.id) as session_count,
		   (SELECT COUNT(*) FROM solana_schema.transactions t WHERE t.user_id = u.id) as tx_count
		 FROM auth_schema.users u %s ORDER BY u.created_at DESC LIMIT 100000`, where),
		args...)
	if err != nil {
		return nil, err
	}
	return rowsToMaps(rows)
}

// ExportTransactions exports all transactions with user info.
func (q *Queries) ExportTransactions(ctx context.Context, params DateRangeParams) ([]map[string]interface{}, error) {
	var conditions []string
	var args []interface{}
	argIdx := 1

	argIdx = addDateFilters(&conditions, &args, argIdx, "t.created_at", params.From, params.To)
	_ = argIdx

	where := ""
	if len(conditions) > 0 {
		where = "WHERE " + strings.Join(conditions, " AND ")
	}

	rows, err := q.pool.Query(ctx,
		fmt.Sprintf(`SELECT t.id, u.wallet_address as user_wallet, t.action::text, t.protocol,
		   t.status::text, t.tx_hash, t.chain, t.estimated_fee, t.actual_fee,
		   t.error_message, t.initiated_at, t.submitted_at, t.confirmed_at, t.created_at
		 FROM solana_schema.transactions t
		 LEFT JOIN auth_schema.users u ON u.id = t.user_id
		 %s ORDER BY t.created_at DESC LIMIT 100000`, where),
		args...)
	if err != nil {
		return nil, err
	}
	return rowsToMaps(rows)
}

// ExportSessions exports all sessions.
func (q *Queries) ExportSessions(ctx context.Context, params DateRangeParams) ([]map[string]interface{}, error) {
	var conditions []string
	var args []interface{}
	argIdx := 1

	argIdx = addDateFilters(&conditions, &args, argIdx, "s.created_at", params.From, params.To)
	_ = argIdx

	where := ""
	if len(conditions) > 0 {
		where = "WHERE " + strings.Join(conditions, " AND ")
	}

	rows, err := q.pool.Query(ctx,
		fmt.Sprintf(`SELECT s.id, s.user_id, s.wallet_address, s.title, s.message_count,
		   s.is_deleted, s.deleted_at, s.pinned, s.created_at, s.updated_at
		 FROM chat_schema.chat_sessions s %s ORDER BY s.created_at DESC LIMIT 100000`, where),
		args...)
	if err != nil {
		return nil, err
	}
	return rowsToMaps(rows)
}

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

// queryCount executes a COUNT query and returns the integer result.
func (q *Queries) queryCount(ctx context.Context, sql string, args ...interface{}) (int, error) {
	var count int
	err := q.pool.QueryRow(ctx, sql, args...).Scan(&count)
	if err != nil {
		return 0, err
	}
	return count, nil
}

// addDateFilters appends date range conditions and arguments.
func addDateFilters(conditions *[]string, args *[]interface{}, argIdx int, column, from, to string) int {
	if from != "" {
		*conditions = append(*conditions, fmt.Sprintf("%s >= $%d", column, argIdx))
		*args = append(*args, from)
		argIdx++
	}
	if to != "" {
		*conditions = append(*conditions, fmt.Sprintf("%s <= $%d", column, argIdx))
		*args = append(*args, to)
		argIdx++
	}
	return argIdx
}

// rowsToMaps converts pgx rows to a slice of maps.
func rowsToMaps(rows pgx.Rows) ([]map[string]interface{}, error) {
	defer rows.Close()

	fieldDescs := rows.FieldDescriptions()
	var result []map[string]interface{}

	for rows.Next() {
		values, err := rows.Values()
		if err != nil {
			return nil, err
		}

		row := make(map[string]interface{}, len(fieldDescs))
		for i, fd := range fieldDescs {
			val := values[i]
			// Convert time.Time to ISO 8601 strings for JSON compatibility
			if t, ok := val.(time.Time); ok {
				val = t.UTC().Format(time.RFC3339)
			}
			row[string(fd.Name)] = val
		}
		result = append(result, row)
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}

	if result == nil {
		result = []map[string]interface{}{}
	}

	return result, nil
}

// --------------------------------------------------------------------------
// Admin session queries
// --------------------------------------------------------------------------

// CreateAdminSession records a new admin login session.
func (q *Queries) CreateAdminSession(ctx context.Context, adminID, adminUsername, ip, userAgent, tokenHash string) (string, error) {
	var id string
	err := q.pool.QueryRow(ctx,
		`INSERT INTO admin_schema.admin_sessions
		     (admin_id, admin_username, ip_address, user_agent, token_hash, login_at, last_active_at, is_active)
		 VALUES ($1, $2, $3, $4, $5, NOW(), NOW(), true)
		 RETURNING id`,
		adminID, adminUsername, ip, userAgent, tokenHash).Scan(&id)
	return id, err
}

// TouchAdminSession updates last_active_at for a session.
func (q *Queries) TouchAdminSession(ctx context.Context, sessionID string) error {
	_, err := q.pool.Exec(ctx,
		`UPDATE admin_schema.admin_sessions SET last_active_at = NOW() WHERE id = $1`,
		sessionID)
	return err
}

// ExpireAdminSession marks a session as inactive (logout).
func (q *Queries) ExpireAdminSession(ctx context.Context, sessionID string) error {
	_, err := q.pool.Exec(ctx,
		`UPDATE admin_schema.admin_sessions SET is_active = false, logout_at = NOW() WHERE id = $1`,
		sessionID)
	return err
}

// ListAdminSessions returns paginated admin sessions with filters.
func (q *Queries) ListAdminSessions(ctx context.Context, params AdminSessionListParams) (*PaginatedResult, error) {
	where := []string{"1=1"}
	args := []interface{}{}
	argN := 1

	if params.AdminUsername != "" {
		where = append(where, fmt.Sprintf("admin_username ILIKE $%d", argN))
		args = append(args, "%"+params.AdminUsername+"%")
		argN++
	}
	if params.Active == "true" {
		where = append(where, "is_active = true")
	} else if params.Active == "false" {
		where = append(where, "is_active = false")
	}
	if params.From != "" {
		where = append(where, fmt.Sprintf("login_at >= $%d", argN))
		args = append(args, params.From)
		argN++
	}
	if params.To != "" {
		where = append(where, fmt.Sprintf("login_at <= $%d", argN))
		args = append(args, params.To)
		argN++
	}

	whereClause := strings.Join(where, " AND ")

	var total int
	countArgs := make([]interface{}, len(args))
	copy(countArgs, args)
	if err := q.pool.QueryRow(ctx,
		`SELECT COUNT(*) FROM admin_schema.admin_sessions WHERE `+whereClause,
		countArgs...).Scan(&total); err != nil {
		return nil, err
	}

	offset := (params.Page - 1) * params.Limit
	args = append(args, params.Limit, offset)
	rows, err := q.pool.Query(ctx,
		`SELECT id, admin_id, admin_username, ip_address, user_agent,
		        login_at, logout_at, last_active_at, is_active
		 FROM admin_schema.admin_sessions
		 WHERE `+whereClause+`
		 ORDER BY login_at DESC
		 LIMIT $`+fmt.Sprintf("%d", argN)+` OFFSET $`+fmt.Sprintf("%d", argN+1),
		args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var sessions []AdminSession
	for rows.Next() {
		var s AdminSession
		if err := rows.Scan(&s.ID, &s.AdminID, &s.AdminUsername, &s.IPAddress,
			&s.UserAgent, &s.LoginAt, &s.LogoutAt, &s.LastActiveAt, &s.IsActive); err != nil {
			return nil, err
		}
		sessions = append(sessions, s)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	totalPages := int(math.Ceil(float64(total) / float64(params.Limit)))
	return &PaginatedResult{
		Data:       sessions,
		Total:      total,
		Page:       params.Page,
		Limit:      params.Limit,
		TotalPages: totalPages,
	}, nil
}

// --------------------------------------------------------------------------
// IP log queries
// --------------------------------------------------------------------------

// InsertIPLog records a login event in admin_schema.ip_logs.
// Called by auth-service on every login attempt.
func (q *Queries) InsertIPLog(ctx context.Context, walletAddress, ip, userAgent, country string, success bool, failureReason string) error {
	var fr *string
	if failureReason != "" {
		fr = &failureReason
	}
	var ua *string
	if userAgent != "" {
		ua = &userAgent
	}
	var co *string
	if country != "" {
		co = &country
	}
	_, err := q.pool.Exec(ctx,
		`INSERT INTO admin_schema.ip_logs
		     (wallet_address, ip_address, user_agent, country, success, failure_reason, logged_at)
		 VALUES ($1, $2, $3, $4, $5, $6, NOW())`,
		walletAddress, ip, ua, co, success, fr)
	return err
}

// ListIPLogs returns paginated IP log entries with optional filters.
func (q *Queries) ListIPLogs(ctx context.Context, params IPLogListParams) (*PaginatedResult, error) {
	where := []string{"1=1"}
	args := []interface{}{}
	argN := 1

	if params.IP != "" {
		where = append(where, fmt.Sprintf("ip_address = $%d", argN))
		args = append(args, params.IP)
		argN++
	}
	if params.Wallet != "" {
		where = append(where, fmt.Sprintf("wallet_address ILIKE $%d", argN))
		args = append(args, "%"+params.Wallet+"%")
		argN++
	}
	if params.Success == "true" {
		where = append(where, "success = true")
	} else if params.Success == "false" {
		where = append(where, "success = false")
	}
	if params.From != "" {
		where = append(where, fmt.Sprintf("logged_at >= $%d", argN))
		args = append(args, params.From)
		argN++
	}
	if params.To != "" {
		where = append(where, fmt.Sprintf("logged_at <= $%d", argN))
		args = append(args, params.To)
		argN++
	}

	whereClause := strings.Join(where, " AND ")

	var total int
	countArgs := make([]interface{}, len(args))
	copy(countArgs, args)
	if err := q.pool.QueryRow(ctx,
		`SELECT COUNT(*) FROM admin_schema.ip_logs WHERE `+whereClause,
		countArgs...).Scan(&total); err != nil {
		return nil, err
	}

	offset := (params.Page - 1) * params.Limit
	args = append(args, params.Limit, offset)
	rows, err := q.pool.Query(ctx,
		`SELECT id, wallet_address, ip_address, user_agent, country, success, failure_reason, logged_at
		 FROM admin_schema.ip_logs
		 WHERE `+whereClause+`
		 ORDER BY logged_at DESC
		 LIMIT $`+fmt.Sprintf("%d", argN)+` OFFSET $`+fmt.Sprintf("%d", argN+1),
		args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var logs []IPLog
	for rows.Next() {
		var l IPLog
		if err := rows.Scan(&l.ID, &l.WalletAddress, &l.IPAddress, &l.UserAgent,
			&l.Country, &l.Success, &l.FailureReason, &l.LoggedAt); err != nil {
			return nil, err
		}
		logs = append(logs, l)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	totalPages := int(math.Ceil(float64(total) / float64(params.Limit)))
	return &PaginatedResult{
		Data:       logs,
		Total:      total,
		Page:       params.Page,
		Limit:      params.Limit,
		TotalPages: totalPages,
	}, nil
}

// GetIPWalletSummary returns wallets associated with a specific IP address.
func (q *Queries) GetIPWalletSummary(ctx context.Context, ip string) (*IPWalletSummary, error) {
	row := q.pool.QueryRow(ctx,
		`SELECT ip_address, wallet_count, login_count, fail_count, first_seen, last_seen, wallets
		 FROM admin_schema.ip_wallet_summary
		 WHERE ip_address = $1`, ip)

	var s IPWalletSummary
	if err := row.Scan(&s.IPAddress, &s.WalletCount, &s.LoginCount,
		&s.FailCount, &s.FirstSeen, &s.LastSeen, &s.Wallets); err != nil {
		return nil, err
	}
	return &s, nil
}

// ListSuspiciousIPs returns IPs with multiple associated wallets (potential fraud).
func (q *Queries) ListSuspiciousIPs(ctx context.Context, minWallets int, limit int) ([]IPWalletSummary, error) {
	rows, err := q.pool.Query(ctx,
		`SELECT ip_address, wallet_count, login_count, fail_count, first_seen, last_seen, wallets
		 FROM admin_schema.ip_wallet_summary
		 WHERE wallet_count >= $1
		 ORDER BY wallet_count DESC, login_count DESC
		 LIMIT $2`,
		minWallets, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []IPWalletSummary
	for rows.Next() {
		var s IPWalletSummary
		if err := rows.Scan(&s.IPAddress, &s.WalletCount, &s.LoginCount,
			&s.FailCount, &s.FirstSeen, &s.LastSeen, &s.Wallets); err != nil {
			return nil, err
		}
		result = append(result, s)
	}
	return result, rows.Err()
}

// --------------------------------------------------------------------------
// Failed login attempt queries
// --------------------------------------------------------------------------

// InsertFailedLoginAttempt records a failed admin login attempt.
func (q *Queries) InsertFailedLoginAttempt(ctx context.Context, username, ip, userAgent, reason string) error {
	var u *string
	if username != "" {
		u = &username
	}
	var ua *string
	if userAgent != "" {
		ua = &userAgent
	}
	var r *string
	if reason != "" {
		r = &reason
	}
	_, err := q.pool.Exec(ctx,
		`INSERT INTO admin_schema.failed_login_attempts
		     (username, ip_address, user_agent, failure_reason, attempted_at)
		 VALUES ($1, $2, $3, $4, NOW())`,
		u, ip, ua, r)
	return err
}

// GetRecentFailedLogins returns failed login attempts in the last N minutes.
func (q *Queries) GetRecentFailedLogins(ctx context.Context, ip string, minutes int) (int, error) {
	var count int
	err := q.pool.QueryRow(ctx,
		`SELECT COUNT(*) FROM admin_schema.failed_login_attempts
		 WHERE ip_address = $1 AND attempted_at >= NOW() - INTERVAL '1 minute' * $2`,
		ip, minutes).Scan(&count)
	return count, err
}

// --------------------------------------------------------------------------
// Analytics queries
// --------------------------------------------------------------------------

// GetAnalyticsTimeseries returns daily aggregated stats for the last N days.
func (q *Queries) GetAnalyticsTimeseries(ctx context.Context, days int) ([]AnalyticsTimeseriesPoint, error) {
	rows, err := q.pool.Query(ctx,
		`WITH date_series AS (
		     SELECT generate_series(
		         (NOW() - INTERVAL '1 day' * $1)::date,
		         NOW()::date,
		         '1 day'::interval
		     )::date AS d
		 )
		 SELECT
		     ds.d::text AS date,
		     COALESCE(SUM(CASE WHEN u.created_at::date = ds.d THEN 1 ELSE 0 END), 0) AS users,
		     COALESCE(SUM(CASE WHEN s.created_at::date = ds.d THEN 1 ELSE 0 END), 0) AS sessions,
		     COALESCE(SUM(CASE WHEN t.created_at::date = ds.d THEN 1 ELSE 0 END), 0) AS tx,
		     COALESCE(SUM(CASE WHEN m.created_at::date = ds.d THEN 1 ELSE 0 END), 0) AS messages
		 FROM date_series ds
		 LEFT JOIN auth_schema.users u ON u.created_at::date = ds.d AND u.is_deleted = false
		 LEFT JOIN chat_schema.chat_sessions s ON s.created_at::date = ds.d AND s.is_deleted = false
		 LEFT JOIN solana_schema.transactions t ON t.created_at::date = ds.d
		 LEFT JOIN chat_schema.chat_messages m ON m.created_at::date = ds.d
		 GROUP BY ds.d
		 ORDER BY ds.d ASC`,
		days)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []AnalyticsTimeseriesPoint
	for rows.Next() {
		var p AnalyticsTimeseriesPoint
		if err := rows.Scan(&p.Date, &p.Users, &p.Sessions, &p.Tx, &p.Messages); err != nil {
			return nil, err
		}
		result = append(result, p)
	}
	return result, rows.Err()
}

// GetTopWalletsByActivity returns top wallets by activity (sessions + tx).
func (q *Queries) GetTopWalletsByActivity(ctx context.Context, limit int) ([]TopWallet, error) {
	rows, err := q.pool.Query(ctx,
		`SELECT
		     u.wallet_address,
		     u.status,
		     u.created_at,
		     COUNT(DISTINCT s.id) AS session_count,
		     COUNT(DISTINCT t.id) AS tx_count
		 FROM auth_schema.users u
		 LEFT JOIN chat_schema.chat_sessions s ON s.wallet_address = u.wallet_address
		 LEFT JOIN solana_schema.transactions t ON t.user_wallet = u.wallet_address
		 WHERE u.is_deleted = false
		 GROUP BY u.wallet_address, u.status, u.created_at
		 ORDER BY (COUNT(DISTINCT s.id) + COUNT(DISTINCT t.id)) DESC
		 LIMIT $1`,
		limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []TopWallet
	for rows.Next() {
		var w TopWallet
		if err := rows.Scan(&w.WalletAddress, &w.Status, &w.CreatedAt, &w.SessionCount, &w.TxCount); err != nil {
			return nil, err
		}
		result = append(result, w)
	}
	return result, rows.Err()
}

// GetTxByProtocol returns transaction counts grouped by protocol.
func (q *Queries) GetTxByProtocol(ctx context.Context) ([]ActionCount, error) {
	rows, err := q.pool.Query(ctx,
		`SELECT COALESCE(protocol, 'unknown') AS action, COUNT(*) AS count
		 FROM solana_schema.transactions
		 WHERE created_at >= NOW() - INTERVAL '30 days'
		 GROUP BY protocol
		 ORDER BY count DESC
		 LIMIT 20`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []ActionCount
	for rows.Next() {
		var ac ActionCount
		if err := rows.Scan(&ac.Action, &ac.Count); err != nil {
			return nil, err
		}
		result = append(result, ac)
	}
	return result, rows.Err()
}

// RefreshIPWalletSummary refreshes the materialized view for IP-wallet aggregates.
func (q *Queries) RefreshIPWalletSummary(ctx context.Context) error {
	_, err := q.pool.Exec(ctx, `REFRESH MATERIALIZED VIEW admin_schema.ip_wallet_summary`)
	return err
}

// ExportAuditLogs exports all admin audit log entries in a date range.
func (q *Queries) ExportAuditLogs(ctx context.Context, params DateRangeParams) ([]map[string]interface{}, error) {
	where := []string{"1=1"}
	args := []interface{}{}
	argN := 1
	if params.From != "" {
		where = append(where, fmt.Sprintf("created_at >= $%d", argN))
		args = append(args, params.From)
		argN++
	}
	if params.To != "" {
		where = append(where, fmt.Sprintf("created_at <= $%d", argN))
		args = append(args, params.To)
		argN++
	}
	_ = argN
	rows, err := q.pool.Query(ctx,
		`SELECT id, admin_username, action, target_type, target_id, ip_address, created_at
		 FROM admin_schema.admin_audit_log
		 WHERE `+strings.Join(where, " AND ")+`
		 ORDER BY created_at DESC
		 LIMIT 50000`,
		args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []map[string]interface{}
	for rows.Next() {
		var id, adminUsername, action string
		var targetType, targetID, ipAddress *string
		var createdAt time.Time
		if err := rows.Scan(&id, &adminUsername, &action, &targetType, &targetID, &ipAddress, &createdAt); err != nil {
			return nil, err
		}
		result = append(result, map[string]interface{}{
			"id":          id,
			"admin":       adminUsername,
			"action":      action,
			"target_type": targetType,
			"target_id":   targetID,
			"ip":          ipAddress,
			"created_at":  createdAt.Format(time.RFC3339),
		})
	}
	return result, rows.Err()
}

// nilIfEmpty returns nil if the string is empty, otherwise returns a pointer to the string.
func nilIfEmpty(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

// ComputeDailyStats aggregates cross-schema counts for a given UTC day.
// The admin service has read access to auth_schema, chat_schema, and solana_schema.
func (q *Queries) ComputeDailyStats(ctx context.Context, date time.Time) (DailyStats, error) {
	dayStart := date.UTC().Truncate(24 * time.Hour)
	dayEnd := dayStart.Add(24 * time.Hour)

	var stats DailyStats
	stats.StatDate = dayStart

	// New users created today (auth_schema.users)
	row := q.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM auth_schema.users
		WHERE created_at >= $1 AND created_at < $2 AND is_deleted = false
	`, dayStart, dayEnd)
	if err := row.Scan(&stats.NewUsers); err != nil {
		return stats, fmt.Errorf("ComputeDailyStats.new_users: %w", err)
	}

	// Active users: distinct wallet_address in auth_schema.login_logs for the day
	row = q.pool.QueryRow(ctx, `
		SELECT COUNT(DISTINCT wallet_address) FROM auth_schema.login_logs
		WHERE created_at >= $1 AND created_at < $2 AND success = true
	`, dayStart, dayEnd)
	if err := row.Scan(&stats.ActiveUsers); err != nil {
		return stats, fmt.Errorf("ComputeDailyStats.active_users: %w", err)
	}

	// New sessions (chat_schema.chat_sessions)
	row = q.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM chat_schema.chat_sessions
		WHERE created_at >= $1 AND created_at < $2
	`, dayStart, dayEnd)
	if err := row.Scan(&stats.NewSessions); err != nil {
		return stats, fmt.Errorf("ComputeDailyStats.new_sessions: %w", err)
	}

	// Total messages (chat_schema.chat_messages)
	row = q.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM chat_schema.chat_messages
		WHERE created_at >= $1 AND created_at < $2
	`, dayStart, dayEnd)
	if err := row.Scan(&stats.TotalMessages); err != nil {
		return stats, fmt.Errorf("ComputeDailyStats.total_messages: %w", err)
	}

	// New transactions (solana_schema.transactions)
	row = q.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM solana_schema.transactions
		WHERE created_at >= $1 AND created_at < $2
	`, dayStart, dayEnd)
	if err := row.Scan(&stats.NewTransactions); err != nil {
		return stats, fmt.Errorf("ComputeDailyStats.new_transactions: %w", err)
	}

	// Confirmed transactions
	row = q.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM solana_schema.transactions
		WHERE created_at >= $1 AND created_at < $2 AND status = 'confirmed'
	`, dayStart, dayEnd)
	if err := row.Scan(&stats.ConfirmedTx); err != nil {
		return stats, fmt.Errorf("ComputeDailyStats.confirmed_tx: %w", err)
	}

	// Failed transactions
	row = q.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM solana_schema.transactions
		WHERE created_at >= $1 AND created_at < $2 AND status IN ('failed', 'error')
	`, dayStart, dayEnd)
	if err := row.Scan(&stats.FailedTx); err != nil {
		return stats, fmt.Errorf("ComputeDailyStats.failed_tx: %w", err)
	}

	return stats, nil
}

// UpsertDailyStats inserts or updates a daily_stats row (keyed on stat_date).
func (q *Queries) UpsertDailyStats(ctx context.Context, s DailyStats) error {
	_, err := q.pool.Exec(ctx, `
		INSERT INTO admin_schema.daily_stats (
			stat_date, new_users, active_users, new_sessions,
			total_messages, new_transactions, confirmed_tx, failed_tx,
			total_fees_lamport, updated_at
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
		ON CONFLICT (stat_date) DO UPDATE SET
			new_users         = EXCLUDED.new_users,
			active_users      = EXCLUDED.active_users,
			new_sessions      = EXCLUDED.new_sessions,
			total_messages    = EXCLUDED.total_messages,
			new_transactions  = EXCLUDED.new_transactions,
			confirmed_tx      = EXCLUDED.confirmed_tx,
			failed_tx         = EXCLUDED.failed_tx,
			total_fees_lamport= EXCLUDED.total_fees_lamport,
			updated_at        = NOW()
	`,
		s.StatDate, s.NewUsers, s.ActiveUsers, s.NewSessions,
		s.TotalMessages, s.NewTransactions, s.ConfirmedTx, s.FailedTx,
		s.TotalFeesLamport,
	)
	if err != nil {
		return fmt.Errorf("UpsertDailyStats: %w", err)
	}
	return nil
}

// GetIssueReports returns a paginated list of user-submitted issue reports.
//
// Default ordering puts open reports first and newest first within each
// state, because the queue's job is "what still needs an answer" — a
// straight created_at DESC buries an unanswered week-old report under
// today's resolved ones.
func (q *Queries) GetIssueReports(ctx context.Context, params IssueReportListParams) (*PaginatedResult, error) {
	offset := (params.Page - 1) * params.Limit

	var conditions []string
	var args []interface{}
	argIdx := 1

	if params.Status != "" {
		conditions = append(conditions, fmt.Sprintf("status = $%d", argIdx))
		args = append(args, params.Status)
		argIdx++
	}
	if params.Category != "" {
		conditions = append(conditions, fmt.Sprintf("category = $%d", argIdx))
		args = append(args, params.Category)
		argIdx++
	}
	if params.Search != "" {
		conditions = append(conditions, fmt.Sprintf(
			"(wallet ILIKE $%d OR subject ILIKE $%d OR description ILIKE $%d)", argIdx, argIdx, argIdx))
		args = append(args, "%"+params.Search+"%")
		argIdx++
	}

	where := ""
	if len(conditions) > 0 {
		where = "WHERE " + strings.Join(conditions, " AND ")
	}

	total, err := q.queryCount(ctx,
		fmt.Sprintf(`SELECT COUNT(*) FROM chat_schema.issue_reports %s`, where), args...)
	if err != nil {
		return nil, err
	}

	dataArgs := append(args, params.Limit, offset)
	rows, err := q.pool.Query(ctx,
		fmt.Sprintf(`SELECT id, wallet, category, subject, description, context,
		 status, admin_note, created_at, updated_at
		 FROM chat_schema.issue_reports
		 %s
		 ORDER BY (status = 'open') DESC, created_at DESC
		 LIMIT $%d OFFSET $%d`, where, argIdx, argIdx+1),
		dataArgs...)
	if err != nil {
		return nil, err
	}

	data, err := rowsToMaps(rows)
	if err != nil {
		return nil, err
	}

	return &PaginatedResult{
		Data:       data,
		Total:      total,
		Page:       params.Page,
		Limit:      params.Limit,
		TotalPages: int(math.Ceil(float64(total) / float64(params.Limit))),
	}, nil
}

// UpdateIssueReport sets a report's status and/or admin note.
//
// The status values are constrained by a CHECK on the table; this rejects
// anything else up front so a typo comes back as a 400 rather than a 500
// from Postgres.
func (q *Queries) UpdateIssueReport(ctx context.Context, id, status, note string) error {
	switch status {
	case "open", "in_progress", "resolved", "dismissed":
	default:
		return fmt.Errorf("invalid status %q", status)
	}
	_, err := q.pool.Exec(ctx,
		`UPDATE chat_schema.issue_reports
		    SET status = $2,
		        admin_note = COALESCE(NULLIF($3, ''), admin_note),
		        updated_at = NOW()
		  WHERE id = $1`, id, status, note)
	return err
}
