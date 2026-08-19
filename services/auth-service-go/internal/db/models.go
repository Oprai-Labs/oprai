package db

import (
	"time"

	"github.com/jackc/pgx/v5/pgtype"
)

// User maps to auth_schema.users.
// LinkedIdentity is one credential belonging to an account (users.id): the
// account's Solana wallet today; an EVM wallet, Telegram, or email tomorrow.
type LinkedIdentity struct {
	ID         string
	AccountID  string
	Type       string
	Chain      pgtype.Text
	Identifier string
	Label      pgtype.Text
	IsPrimary  bool
	VerifiedAt time.Time
	CreatedAt  time.Time
}

// ToJSON renders a linked identity for API responses.
func (li *LinkedIdentity) ToJSON() map[string]any {
	m := map[string]any{
		"id":         li.ID,
		"type":       li.Type,
		"identifier": li.Identifier,
		"isPrimary":  li.IsPrimary,
		"verifiedAt": li.VerifiedAt.UTC().Format(time.RFC3339),
	}
	if li.Chain.Valid {
		m["chain"] = li.Chain.String
	}
	if li.Label.Valid {
		m["label"] = li.Label.String
	}
	return m
}

type User struct {
	ID                     string
	WalletAddress          string
	Chain                  string
	DisplayName            pgtype.Text
	RiskTolerance          pgtype.Text
	PreferredProtocols     []string
	AutoSuggestionsAllowed bool
	Role                   string
	Status                 string
	IsDeleted              bool
	StatusReason           pgtype.Text
	StatusChangedAt        pgtype.Timestamptz
	StatusChangedBy        pgtype.Text
	CreatedAt              time.Time
	UpdatedAt              time.Time
}

// UserJSON is the JSON-serializable representation of a User, matching the
// Node.js auth-service response format (camelCase keys).
type UserJSON struct {
	ID                     string   `json:"id"`
	WalletAddress          string   `json:"walletAddress"`
	Chain                  string   `json:"chain"`
	DisplayName            *string  `json:"displayName"`
	RiskTolerance          *string  `json:"riskTolerance"`
	PreferredProtocols     []string `json:"preferredProtocols"`
	AutoSuggestionsAllowed bool     `json:"autoSuggestionsAllowed"`
	Role                   string   `json:"role"`
	Status                 string   `json:"status"`
	CreatedAt              string   `json:"createdAt"`
	UpdatedAt              string   `json:"updatedAt"`
}

// ToJSON converts a User to its JSON representation.
func (u *User) ToJSON() UserJSON {
	j := UserJSON{
		ID:                     u.ID,
		WalletAddress:          u.WalletAddress,
		Chain:                  u.Chain,
		AutoSuggestionsAllowed: u.AutoSuggestionsAllowed,
		Role:                   u.Role,
		Status:                 u.Status,
		PreferredProtocols:     u.PreferredProtocols,
		CreatedAt:              u.CreatedAt.UTC().Format(time.RFC3339Nano),
		UpdatedAt:              u.UpdatedAt.UTC().Format(time.RFC3339Nano),
	}
	if u.DisplayName.Valid {
		j.DisplayName = &u.DisplayName.String
	}
	if u.RiskTolerance.Valid {
		j.RiskTolerance = &u.RiskTolerance.String
	}
	if j.PreferredProtocols == nil {
		j.PreferredProtocols = []string{}
	}
	return j
}

// SpendingLimits maps to auth_schema.spending_limits.
// maxPerTxUsd == 0 means unlimited; maxPerDayUsd == 0 means unlimited.
type SpendingLimits struct {
	ID            string
	UserID        string
	WalletAddress string
	MaxPerTxUsd   float64
	MaxPerDayUsd  float64
	CreatedAt     time.Time
	UpdatedAt     time.Time
}

// SpendingLimitsJSON is the JSON-serializable form (camelCase for frontend).
type SpendingLimitsJSON struct {
	MaxPerTxUsd  float64 `json:"maxPerTxUsd"`
	MaxPerDayUsd float64 `json:"maxPerDayUsd"`
}

// AuditEvent maps to auth_schema.audit_trail.
type AuditEvent struct {
	EventType            string
	Severity             string // debug | info | warning | error | critical
	EntityType           string
	EntityID             string
	UserID               string
	WalletAddress        string
	SessionID            string
	EventData            map[string]any
	IPAddress            string
	UserAgent            string
	RequestMethod        string
	RequestPath          string
	RequestID            string
	ResponseStatus       int
	ResponseTimeMs       float64
	TransactionSignature string
	TransactionAmount    float64
	TransactionToken     string
}

// LoginLog maps to auth_schema.login_logs.
type LoginLog struct {
	ID            string
	UserID        string
	WalletAddress string
	IPAddress     string
	UserAgent     string
	Country       pgtype.Text
	Success       bool
	FailureReason pgtype.Text
	CreatedAt     time.Time
}
