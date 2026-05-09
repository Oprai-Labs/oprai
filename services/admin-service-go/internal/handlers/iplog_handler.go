package handlers

import (
	"log/slog"
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// IPLogHandler handles IP login log endpoints.
type IPLogHandler struct {
	queries *db.Queries
}

// NewIPLogHandler creates a new IPLogHandler.
func NewIPLogHandler(queries *db.Queries) *IPLogHandler {
	return &IPLogHandler{queries: queries}
}

// ListIPLogs returns a paginated list of IP login logs.
func (h *IPLogHandler) ListIPLogs(w http.ResponseWriter, r *http.Request) {
	params := db.IPLogListParams{
		PaginationParams: parsePagination(r),
		IP:               r.URL.Query().Get("ip"),
		Wallet:           r.URL.Query().Get("wallet"),
		Success:          r.URL.Query().Get("success"),
		From:             r.URL.Query().Get("from"),
		To:               r.URL.Query().Get("to"),
	}

	result, err := h.queries.ListIPLogs(r.Context(), params)
	if err != nil {
		slog.Error("ip logs list error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch IP logs"})
		return
	}

	writeJSON(w, http.StatusOK, result)
}

// GetByIP returns the wallet summary for a specific IP address.
func (h *IPLogHandler) GetByIP(w http.ResponseWriter, r *http.Request) {
	ip := chi.URLParam(r, "ip")

	summary, err := h.queries.GetIPWalletSummary(r.Context(), ip)
	if err != nil {
		// If not found in materialized view, return empty summary.
		writeJSON(w, http.StatusOK, map[string]interface{}{
			"ipAddress":   ip,
			"walletCount": 0,
			"loginCount":  0,
			"failCount":   0,
			"wallets":     []string{},
		})
		return
	}

	writeJSON(w, http.StatusOK, summary)
}

// ListSuspiciousIPs returns IPs with multiple wallets (potential fraud).
func (h *IPLogHandler) ListSuspiciousIPs(w http.ResponseWriter, r *http.Request) {
	minWallets := 2
	if v := r.URL.Query().Get("min_wallets"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			minWallets = n
		}
	}

	limit := 50
	if v := r.URL.Query().Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 200 {
			limit = n
		}
	}

	results, err := h.queries.ListSuspiciousIPs(r.Context(), minWallets, limit)
	if err != nil {
		slog.Error("suspicious IPs list error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch suspicious IPs"})
		return
	}

	if results == nil {
		results = []db.IPWalletSummary{}
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"data":       results,
		"minWallets": minWallets,
	})
}

// RefreshIPSummary triggers a refresh of the ip_wallet_summary materialized view.
func (h *IPLogHandler) RefreshIPSummary(w http.ResponseWriter, r *http.Request) {
	if err := h.queries.RefreshIPWalletSummary(r.Context()); err != nil {
		slog.Error("ip summary refresh error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to refresh IP summary"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true, "message": "IP wallet summary refreshed"})
}
