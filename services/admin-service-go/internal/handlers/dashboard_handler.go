package handlers

import (
	"log/slog"
	"net/http"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// DashboardHandler handles dashboard statistics endpoints.
type DashboardHandler struct {
	queries *db.Queries
}

// NewDashboardHandler creates a new DashboardHandler.
func NewDashboardHandler(queries *db.Queries) *DashboardHandler {
	return &DashboardHandler{queries: queries}
}

// GetStats returns aggregated dashboard statistics from all schemas.
func (h *DashboardHandler) GetStats(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	stats, err := h.queries.GetDashboardStats(ctx)
	if err != nil {
		slog.Error("dashboard stats error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch dashboard stats"})
		return
	}

	writeJSON(w, http.StatusOK, stats)
}
