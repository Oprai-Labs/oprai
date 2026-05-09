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

// ── Internal endpoints (gated by X-Internal-Api-Key) ────────────────────────────
//
// These are called by solana-service-rs immediately before building/submitting
// a fund-moving transaction. The frontend check is informational only; the
// hard-stop happens here so a malicious client cannot bypass the cap by
// hitting the gateway directly.

type checkSpendingRequest struct {
	Wallet    string  `json:"wallet"`
	AmountUsd float64 `json:"amountUsd"`
}

type checkSpendingResponse struct {
	Allowed         bool    `json:"allowed"`
	Reason          string  `json:"reason,omitempty"`     // "" | "per_tx" | "daily"
	LimitUsd        float64 `json:"limitUsd,omitempty"`
	CurrentDailyUsd float64 `json:"currentDailyUsd"`
}

// HandleCheckSpending — POST /internal/spending/check
// Pure read; does not mutate the daily counter. Returns whether amountUsd
// would be allowed under the user's per-tx and daily caps.
func (h *SpendingHandler) HandleCheckSpending(w http.ResponseWriter, r *http.Request) {
	var req checkSpendingRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "Invalid request body")
		return
	}
	if req.Wallet == "" || req.AmountUsd < 0 {
		writeError(w, http.StatusBadRequest, "wallet and non-negative amountUsd are required")
		return
	}

	sl, err := h.queries.GetSpendingLimits(r.Context(), req.Wallet)
	if err != nil {
		slog.Error("CheckSpending: GetSpendingLimits failed", "wallet", req.Wallet, "error", err)
		writeError(w, http.StatusInternalServerError, "limit lookup failed")
		return
	}
	currentDaily, err := h.queries.GetTodaySpendingTotal(r.Context(), req.Wallet)
	if err != nil {
		slog.Error("CheckSpending: GetTodaySpendingTotal failed", "wallet", req.Wallet, "error", err)
		writeError(w, http.StatusInternalServerError, "daily total lookup failed")
		return
	}

	resp := checkSpendingResponse{Allowed: true, CurrentDailyUsd: currentDaily}
	if sl != nil {
		if sl.MaxPerTxUsd > 0 && req.AmountUsd > sl.MaxPerTxUsd {
			resp.Allowed = false
			resp.Reason = "per_tx"
			resp.LimitUsd = sl.MaxPerTxUsd
		} else if sl.MaxPerDayUsd > 0 && (currentDaily+req.AmountUsd) > sl.MaxPerDayUsd {
			resp.Allowed = false
			resp.Reason = "daily"
			resp.LimitUsd = sl.MaxPerDayUsd
		}
	}
	writeJSON(w, http.StatusOK, resp)
}

type commitSpendingRequest struct {
	Wallet    string  `json:"wallet"`
	AmountUsd float64 `json:"amountUsd"`
}

type commitSpendingResponse struct {
	NewDailyTotal float64 `json:"newDailyTotal"`
}

// HandleCommitSpending — POST /internal/spending/commit
// Atomically increments today's counter for `wallet` by `amountUsd`. Called
// from solana-service-rs after /actions/submit succeeds (i.e. the user has
// actually signed and we have an on-chain signature). The atomic UPSERT
// guarantees concurrent commits cannot lose updates.
func (h *SpendingHandler) HandleCommitSpending(w http.ResponseWriter, r *http.Request) {
	var req commitSpendingRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "Invalid request body")
		return
	}
	if req.Wallet == "" || req.AmountUsd <= 0 {
		writeError(w, http.StatusBadRequest, "wallet and positive amountUsd are required")
		return
	}
	total, err := h.queries.IncrementTodaySpending(r.Context(), req.Wallet, req.AmountUsd)
	if err != nil {
		slog.Error("CommitSpending: IncrementTodaySpending failed", "wallet", req.Wallet, "error", err)
		writeError(w, http.StatusInternalServerError, "daily total update failed")
		return
	}
	writeJSON(w, http.StatusOK, commitSpendingResponse{NewDailyTotal: total})
}
