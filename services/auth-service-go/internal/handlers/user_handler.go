package handlers

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/oprai/oprai/services/auth-service-go/internal/audit"
	"github.com/oprai/oprai/services/auth-service-go/internal/config"
	"github.com/oprai/oprai/services/auth-service-go/internal/db"
	"github.com/oprai/oprai/services/auth-service-go/internal/middleware"
)

// UserHandler handles user profile CRUD endpoints.
type UserHandler struct {
	queries     *db.Queries
	auditLogger *audit.Logger
	cfg         *config.Config
}

// NewUserHandler creates a new UserHandler.
func NewUserHandler(queries *db.Queries, auditLogger *audit.Logger, cfg *config.Config) *UserHandler {
	return &UserHandler{queries: queries, auditLogger: auditLogger, cfg: cfg}
}

// HandleGetMe handles GET /users/me.
// Returns the authenticated user's profile.
func (h *UserHandler) HandleGetMe(w http.ResponseWriter, r *http.Request) {
	wallet := middleware.WalletFromContext(r.Context())
	if wallet == "" {
		writeError(w, http.StatusUnauthorized, "Unauthorized")
		return
	}

	user, err := h.queries.GetUserByWallet(r.Context(), wallet)
	if err != nil {
		slog.Error("Failed to get user by wallet",
			"wallet", wallet,
			"error", err,
		)
		writeError(w, http.StatusInternalServerError, "Failed to retrieve user")
		return
	}
	if user == nil {
		writeError(w, http.StatusNotFound, "User not found")
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{"user": user.ToJSON()})
}

// updateMeRequest is the request body for PUT /users/me.
type updateMeRequest struct {
	DisplayName            *string  `json:"displayName,omitempty"`
	RiskTolerance          *string  `json:"riskTolerance,omitempty"`
	PreferredProtocols     *[]string `json:"preferredProtocols,omitempty"`
	AutoSuggestionsAllowed *bool    `json:"autoSuggestionsAllowed,omitempty"`
}

// HandleUpdateMe handles PUT /users/me.
// Updates the authenticated user's profile.
func (h *UserHandler) HandleUpdateMe(w http.ResponseWriter, r *http.Request) {
	wallet := middleware.WalletFromContext(r.Context())
	if wallet == "" {
		writeError(w, http.StatusUnauthorized, "Unauthorized")
		return
	}

	var req updateMeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "Invalid request body")
		return
	}

	// Validate field constraints
	if req.DisplayName != nil && len(*req.DisplayName) > 100 {
		writeError(w, http.StatusBadRequest, "displayName must be at most 100 characters")
		return
	}
	if req.RiskTolerance != nil && len(*req.RiskTolerance) > 50 {
		writeError(w, http.StatusBadRequest, "riskTolerance must be at most 50 characters")
		return
	}

	upd := db.UserUpdate{
		DisplayName:            req.DisplayName,
		RiskTolerance:          req.RiskTolerance,
		PreferredProtocols:     req.PreferredProtocols,
		AutoSuggestionsAllowed: req.AutoSuggestionsAllowed,
	}

	user, err := h.queries.UpdateUser(r.Context(), wallet, upd)
	if err != nil {
		slog.Error("Failed to update user profile",
			"wallet", wallet,
			"error", err,
		)
		writeError(w, http.StatusInternalServerError, "Failed to update user")
		return
	}
	if user == nil {
		writeError(w, http.StatusNotFound, "User not found")
		return
	}

	h.auditLogger.LogRequest(r, db.AuditEvent{
		EventType:     "preferences_updated",
		Severity:      "info",
		EntityType:    "user",
		EntityID:      user.ID,
		UserID:        user.ID,
		WalletAddress: wallet,
	}, h.cfg.TrustProxyHeaders)

	writeJSON(w, http.StatusOK, map[string]any{"user": user.ToJSON()})
}

// HandleGetByWallet handles GET /users/:wallet.
// Returns the authenticated user's own profile by wallet address.
// Users can only fetch their own profile to prevent wallet enumeration.
func (h *UserHandler) HandleGetByWallet(w http.ResponseWriter, r *http.Request) {
	walletParam := chi.URLParam(r, "wallet")
	if walletParam == "" {
		writeError(w, http.StatusBadRequest, "wallet param is required")
		return
	}

	// Enforce self-only access: only the owner may retrieve their own profile.
	// This prevents authenticated users from enumerating other wallets' data.
	authenticatedWallet := middleware.WalletFromContext(r.Context())
	if authenticatedWallet != walletParam {
		writeError(w, http.StatusForbidden, "Forbidden")
		return
	}

	user, err := h.queries.GetUserByWallet(r.Context(), walletParam)
	if err != nil {
		slog.Error("Failed to get user by wallet",
			"wallet", walletParam,
			"error", err,
		)
		writeError(w, http.StatusInternalServerError, "Failed to retrieve user")
		return
	}
	if user == nil {
		writeError(w, http.StatusNotFound, "User not found")
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{"user": user.ToJSON()})
}

// HandleCreateUser handles POST /users.
// Creates or finds a user by wallet address.
func (h *UserHandler) HandleCreateUser(w http.ResponseWriter, r *http.Request) {
	var req struct {
		WalletAddress string `json:"walletAddress"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "Invalid request body")
		return
	}

	wallet := req.WalletAddress
	if wallet == "" {
		// Fall back to authenticated wallet
		wallet = middleware.WalletFromContext(r.Context())
	}
	if wallet == "" {
		writeError(w, http.StatusBadRequest, "Missing walletAddress")
		return
	}

	user, err := h.queries.GetOrCreateUser(r.Context(), wallet)
	if err != nil {
		slog.Error("Failed to get or create user",
			"wallet", wallet,
			"error", err,
		)
		writeError(w, http.StatusInternalServerError, "Failed to create user")
		return
	}

	h.auditLogger.LogRequest(r, db.AuditEvent{
		EventType:     "signup",
		Severity:      "info",
		EntityType:    "user",
		EntityID:      user.ID,
		UserID:        user.ID,
		WalletAddress: wallet,
	}, h.cfg.TrustProxyHeaders)

	writeJSON(w, http.StatusOK, map[string]any{"user": user.ToJSON()})
}

// HandlePatchByWallet handles PATCH /users/:wallet.
// Updates a user profile. Only the owner can update their own profile.
func (h *UserHandler) HandlePatchByWallet(w http.ResponseWriter, r *http.Request) {
	walletParam := chi.URLParam(r, "wallet")
	if walletParam == "" {
		writeError(w, http.StatusBadRequest, "wallet param is required")
		return
	}

	// Only allow users to update their own profile
	authenticatedWallet := middleware.WalletFromContext(r.Context())
	if authenticatedWallet != walletParam {
		writeError(w, http.StatusForbidden, "Forbidden")
		return
	}

	var req updateMeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "Invalid request body")
		return
	}

	// Validate field constraints
	if req.DisplayName != nil && len(*req.DisplayName) > 100 {
		writeError(w, http.StatusBadRequest, "displayName must be at most 100 characters")
		return
	}
	if req.RiskTolerance != nil && len(*req.RiskTolerance) > 50 {
		writeError(w, http.StatusBadRequest, "riskTolerance must be at most 50 characters")
		return
	}

	upd := db.UserUpdate{
		DisplayName:            req.DisplayName,
		RiskTolerance:          req.RiskTolerance,
		PreferredProtocols:     req.PreferredProtocols,
		AutoSuggestionsAllowed: req.AutoSuggestionsAllowed,
	}

	user, err := h.queries.UpdateUser(r.Context(), walletParam, upd)
	if err != nil {
		slog.Error("Failed to update user profile",
			"wallet", walletParam,
			"error", err,
		)
		writeError(w, http.StatusInternalServerError, "Failed to update user")
		return
	}
	if user == nil {
		writeError(w, http.StatusNotFound, "User not found")
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{"user": user.ToJSON()})
}
