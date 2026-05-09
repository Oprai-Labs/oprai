package db

import "time"

// PaginationParams holds pagination query parameters.
type PaginationParams struct {
	Page  int
	Limit int
}

// PaginatedResult holds a paginated query result.
type PaginatedResult struct {
	Data       interface{} `json:"data"`
	Total      int         `json:"total"`
	Page       int         `json:"page"`
	Limit      int         `json:"limit"`
	TotalPages int         `json:"totalPages"`
}

// DateRangeParams extends PaginationParams with date range filters.
type DateRangeParams struct {
	PaginationParams
	From string
	To   string
}

// UserListParams holds query parameters for listing users.
type UserListParams struct {
	PaginationParams
	Search string
	Sort   string
	Order  string
	Status string
	Role   string
	From   string
	To     string
}

// TransactionListParams holds query parameters for listing transactions.
type TransactionListParams struct {
	PaginationParams
	Status   string
	Action   string
	Protocol string
	Search   string
	From     string
	To       string
}

// SessionListParams holds query parameters for listing sessions.
type SessionListParams struct {
	PaginationParams
	Search string
	From   string
	To     string
}

// AuditLogListParams holds query parameters for listing audit logs.
type AuditLogListParams struct {
	PaginationParams
	Admin      string
	Action     string
	TargetType string
	Search     string
	From       string
	To         string
}

// AuditLogEntry represents a new audit log entry to be created.
type AuditLogEntry struct {
	AdminID       string
	AdminUsername  string
	Action        string
	TargetType    string
	TargetID      string
	Details       map[string]interface{}
	IPAddress     string
}

// AdminUser represents a row from admin_schema.admin_users.
type AdminUser struct {
	ID                 string     `json:"id"`
	Username           string     `json:"username"`
	PasswordHash       string     `json:"-"`
	Role               string     `json:"role"`
	MustChangePassword bool       `json:"mustChangePassword"`
	LastLoginAt        *time.Time `json:"lastLoginAt"`
	CreatedAt          time.Time  `json:"createdAt"`
	UpdatedAt          time.Time  `json:"updatedAt"`
}

// AdminUserPublic is a safe-to-serialize version of AdminUser (no password hash).
type AdminUserPublic struct {
	ID                 string     `json:"id"`
	Username           string     `json:"username"`
	Role               string     `json:"role"`
	MustChangePassword bool       `json:"mustChangePassword"`
	LastLoginAt        *time.Time `json:"lastLoginAt"`
	CreatedAt          time.Time  `json:"createdAt"`
}

// RecentUser is a compact user row returned in dashboard stats.
type RecentUser struct {
	ID            string     `json:"id"`
	WalletAddress string     `json:"wallet_address"`
	DisplayName   *string    `json:"display_name"`
	Role          string     `json:"role"`
	Status        string     `json:"status"`
	CreatedAt     time.Time  `json:"created_at"`
}

// RecentTransaction is a compact transaction row returned in dashboard stats.
type RecentTransaction struct {
	ID          string     `json:"id"`
	UserID      string     `json:"user_id"`
	UserWallet  *string    `json:"user_wallet"`
	Action      string     `json:"action"`
	Protocol    *string    `json:"protocol"`
	Status      string     `json:"status"`
	TxHash      *string    `json:"tx_hash"`
	CreatedAt   time.Time  `json:"created_at"`
}

// DashboardStats holds the aggregated dashboard statistics.
type DashboardStats struct {
	TotalUsers        int                 `json:"totalUsers"`
	TotalSessions     int                 `json:"totalSessions"`
	TotalTransactions int                 `json:"totalTransactions"`
	TotalMessages     int                 `json:"totalMessages"`
	UsersToday        int                 `json:"usersToday"`
	TxToday           int                 `json:"txToday"`
	SessionsActive    int                 `json:"sessionsActive"`
	MessagesToday     int                 `json:"messagesToday"`
	Trends            DashboardTrends     `json:"trends"`
	TxByStatus        []StatusCount       `json:"txByStatus"`
	TxByAction        []ActionCount       `json:"txByAction"`
	RecentUsers       []RecentUser        `json:"recentUsers"`
	RecentTx          []RecentTransaction `json:"recentTransactions"`
}

// DashboardTrends holds 7-day-ago comparison counts.
type DashboardTrends struct {
	Users7dAgo    int `json:"users7dAgo"`
	Tx7dAgo       int `json:"tx7dAgo"`
	Sessions7dAgo int `json:"sessions7dAgo"`
	Messages7dAgo int `json:"messages7dAgo"`
}

// StatusCount represents a status with its count.
type StatusCount struct {
	Status string `json:"status"`
	Count  int    `json:"count"`
}

// ActionCount represents an action with its count.
type ActionCount struct {
	Action string `json:"action"`
	Count  int    `json:"count"`
}

// AdminSession represents a row from admin_schema.admin_sessions.
type AdminSession struct {
	ID            string     `json:"id"`
	AdminID       string     `json:"adminId"`
	AdminUsername string     `json:"adminUsername"`
	IPAddress     string     `json:"ipAddress"`
	UserAgent     *string    `json:"userAgent"`
	LoginAt       time.Time  `json:"loginAt"`
	LogoutAt      *time.Time `json:"logoutAt"`
	LastActiveAt  time.Time  `json:"lastActiveAt"`
	IsActive      bool       `json:"isActive"`
}

// IPLog represents a row from admin_schema.ip_logs.
type IPLog struct {
	ID            string     `json:"id"`
	WalletAddress string     `json:"walletAddress"`
	IPAddress     string     `json:"ipAddress"`
	UserAgent     *string    `json:"userAgent"`
	Country       *string    `json:"country"`
	Success       bool       `json:"success"`
	FailureReason *string    `json:"failureReason"`
	LoggedAt      time.Time  `json:"loggedAt"`
}

// IPWalletSummary represents a row from admin_schema.ip_wallet_summary.
type IPWalletSummary struct {
	IPAddress   string    `json:"ipAddress"`
	WalletCount int       `json:"walletCount"`
	LoginCount  int       `json:"loginCount"`
	FailCount   int       `json:"failCount"`
	FirstSeen   time.Time `json:"firstSeen"`
	LastSeen    time.Time `json:"lastSeen"`
	Wallets     []string  `json:"wallets"`
}

// FailedLoginAttempt represents a row from admin_schema.failed_login_attempts.
type FailedLoginAttempt struct {
	ID            string     `json:"id"`
	Username      *string    `json:"username"`
	IPAddress     string     `json:"ipAddress"`
	FailureReason *string    `json:"failureReason"`
	AttemptedAt   time.Time  `json:"attemptedAt"`
}

// IPLogListParams holds query parameters for listing IP logs.
type IPLogListParams struct {
	PaginationParams
	IP      string
	Wallet  string
	Success string
	From    string
	To      string
}

// AdminSessionListParams holds query parameters for listing admin sessions.
type AdminSessionListParams struct {
	PaginationParams
	AdminUsername string
	Active        string
	From          string
	To            string
}

// AnalyticsTimeseriesPoint holds a single data point for time-series analytics.
type AnalyticsTimeseriesPoint struct {
	Date     string `json:"date"`
	Users    int    `json:"users"`
	Sessions int    `json:"sessions"`
	Tx       int    `json:"tx"`
	Messages int    `json:"messages"`
}

// TopWallet holds per-wallet activity counts for analytics.
type TopWallet struct {
	WalletAddress string    `json:"walletAddress"`
	Status        string    `json:"status"`
	CreatedAt     time.Time `json:"createdAt"`
	SessionCount  int       `json:"sessionCount"`
	TxCount       int       `json:"txCount"`
}

// UserDetail is a single user row with cross-schema activity counts.
type UserDetail struct {
	ID            string     `json:"id"`
	WalletAddress string     `json:"walletAddress"`
	DisplayName   *string    `json:"displayName"`
	Email         *string    `json:"email"`
	Status        string     `json:"status"`
	Role          string     `json:"role"`
	CreatedAt     time.Time  `json:"createdAt"`
	UpdatedAt     time.Time  `json:"updatedAt"`
	IsDeleted     bool       `json:"isDeleted"`
	SessionCount  int        `json:"sessionCount"`
	TxCount       int        `json:"txCount"`
	MessageCount  int        `json:"messageCount"`
}

// RecentSession is a compact session row embedded in UserOverview.
type RecentSession struct {
	ID           string     `json:"id"`
	Title        *string    `json:"title"`
	MessageCount int        `json:"messageCount"`
	IsDeleted    bool       `json:"isDeleted"`
	Pinned       bool       `json:"pinned"`
	CreatedAt    time.Time  `json:"createdAt"`
	UpdatedAt    time.Time  `json:"updatedAt"`
}

// UserOverview extends UserDetail with recent activity.
type UserOverview struct {
	UserDetail
	RecentSessions []RecentSession     `json:"recentSessions"`
	RecentTx       []RecentTransaction `json:"recentTx"`
	RecentIPs      []string            `json:"recentIps"`
}

// TransactionDetail is a single transaction row with joined user info.
type TransactionDetail struct {
	ID          string     `json:"id"`
	UserID      string     `json:"userId"`
	UserWallet  *string    `json:"userWallet"`
	DisplayName *string    `json:"displayName"`
	Action      string     `json:"action"`
	Protocol    *string    `json:"protocol"`
	Status      string     `json:"status"`
	TxHash      *string    `json:"txHash"`
	Amount      *string    `json:"amount"`
	CreatedAt   time.Time  `json:"createdAt"`
	UpdatedAt   time.Time  `json:"updatedAt"`
}

// AnalyticsResponse holds all analytics data.
type AnalyticsResponse struct {
	Timeseries    []AnalyticsTimeseriesPoint `json:"timeseries"`
	TopWallets    []TopWallet                `json:"topWallets"`
	TxByProtocol  []ActionCount              `json:"txByProtocol"`
	SuspiciousIPs []IPWalletSummary          `json:"suspiciousIPs"`
}

// DailyStats maps to admin_schema.daily_stats.
type DailyStats struct {
	StatDate          time.Time
	NewUsers          int
	ActiveUsers       int
	NewSessions       int
	TotalMessages     int
	NewTransactions   int
	ConfirmedTx       int
	FailedTx          int
	TotalFeesLamport  int64
}
