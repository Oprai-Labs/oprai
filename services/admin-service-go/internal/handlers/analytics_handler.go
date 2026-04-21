package handlers

import (
	"log/slog"
	"net/http"
	"strconv"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// AnalyticsHandler handles analytics and reporting endpoints.
type AnalyticsHandler struct {
	queries *db.Queries
}

// NewAnalyticsHandler creates a new AnalyticsHandler.
func NewAnalyticsHandler(queries *db.Queries) *AnalyticsHandler {
	return &AnalyticsHandler{queries: queries}
}

// GetTimeseries returns daily aggregated stats for charting.
func (h *AnalyticsHandler) GetTimeseries(w http.ResponseWriter, r *http.Request) {
	days := 30
	if v := r.URL.Query().Get("days"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 365 {
			days = n
		}
	}

	timeseries, err := h.queries.GetAnalyticsTimeseries(r.Context(), days)
	if err != nil {
		slog.Error("analytics timeseries error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch analytics"})
		return
	}

	if timeseries == nil {
		timeseries = []db.AnalyticsTimeseriesPoint{}
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"timeseries": timeseries,
		"days":       days,
	})
}

// GetTopWallets returns top wallets by activity.
func (h *AnalyticsHandler) GetTopWallets(w http.ResponseWriter, r *http.Request) {
	limit := 20
	if v := r.URL.Query().Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 100 {
			limit = n
		}
	}

	wallets, err := h.queries.GetTopWalletsByActivity(r.Context(), limit)
	if err != nil {
		slog.Error("top wallets error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch top wallets"})
		return
	}

	if wallets == nil {
		wallets = []db.TopWallet{}
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{"data": wallets})
}

// GetTxByProtocol returns transaction breakdown by protocol.
func (h *AnalyticsHandler) GetTxByProtocol(w http.ResponseWriter, r *http.Request) {
	data, err := h.queries.GetTxByProtocol(r.Context())
	if err != nil {
		slog.Error("tx by protocol error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch protocol stats"})
		return
	}

	if data == nil {
		data = []db.ActionCount{}
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{"data": data})
}
