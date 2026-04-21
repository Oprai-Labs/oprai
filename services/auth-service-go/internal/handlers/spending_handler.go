package handlers

import (
	"encoding/json"
	"log/slog"
	"math"
	"net/http"

	"github.com/oprai/oprai/services/auth-service-go/internal/db"
	"github.com/oprai/oprai/services/auth-service-go/internal/middleware"
)

// SpendingHandler handles spending limit endpoints for authenticated users.
type SpendingHandler struct {
	queries *db.Queries
}

// NewSpendingHandler creates a new SpendingHandler.
func NewSpendingHandler(queries *db.Queries) *SpendingHandler {
	return &SpendingHandler{queries: queries}
}

// HandleGetSpendingLimits handles GET /users/me/spending-limits.
// Returns the user's current spending limits (defaults to 0/unlimited if not set).
func (h *SpendingHandler) HandleGetSpendingLimits(w http.ResponseWriter, r *http.Request) {
	wallet := middleware.WalletFromContext(r.Context())
	if wallet == "" {
		writeError(w, http.StatusUnauthorized, "Unauthorized")
		return
	}

	sl, err := h.queries.GetSpendingLimits(r.Context(), wallet)
	if err != nil {
		slog.Error("Failed to get spending limits", "wallet", wallet, "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to retrieve spending limits")
		return
	}

	// If no limits are stored, return the defaults (0 = unlimited).
	resp := db.SpendingLimitsJSON{MaxPerTxUsd: 0, MaxPerDayUsd: 0}
	if sl != nil {
		resp.MaxPerTxUsd = sl.MaxPerTxUsd
		resp.MaxPerDayUsd = sl.MaxPerDayUsd
	}

	writeJSON(w, http.StatusOK, resp)
}

// spendingLimitsRequest is the request body for PUT /users/me/spending-limits.
type spendingLimitsRequest struct {
	MaxPerTxUsd  float64 `json:"maxPerTxUsd"`
	MaxPerDayUsd float64 `json:"maxPerDayUsd"`
}

// HandleUpsertSpendingLimits handles PUT /users/me/spending-limits.
// Creates or replaces the spending limits for the authenticated user.
func (h *SpendingHandler) HandleUpsertSpendingLimits(w http.ResponseWriter, r *http.Request) {
	wallet := middleware.WalletFromContext(r.Context())
	if wallet == "" {
		writeError(w, http.StatusUnauthorized, "Unauthorized")
		return
	}

	var req spendingLimitsRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "Invalid request body")
		return
	}

	// Clamp negative values to 0 (0 means unlimited).
	req.MaxPerTxUsd = math.Max(0, req.MaxPerTxUsd)
	req.MaxPerDayUsd = math.Max(0, req.MaxPerDayUsd)

	// Resolve user ID from wallet.
	user, err := h.queries.GetUserByWallet(r.Context(), wallet)
	if err != nil || user == nil {
		writeError(w, http.StatusNotFound, "User not found")
		return
	}

	sl, err := h.queries.UpsertSpendingLimits(r.Context(), user.ID, wallet, req.MaxPerTxUsd, req.MaxPerDayUsd)
	if err != nil {
		slog.Error("Failed to upsert spending limits", "wallet", wallet, "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to update spending limits")
		return
	}

	writeJSON(w, http.StatusOK, db.SpendingLimitsJSON{
		MaxPerTxUsd:  sl.MaxPerTxUsd,
		MaxPerDayUsd: sl.MaxPerDayUsd,
	})
}
