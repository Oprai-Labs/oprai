package handlers

import (
	"log/slog"
	"net/http"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// AuditHandler handles audit log endpoints.
type AuditHandler struct {
	queries *db.Queries
}

// NewAuditHandler creates a new AuditHandler.
func NewAuditHandler(queries *db.Queries) *AuditHandler {
	return &AuditHandler{queries: queries}
}

// ListAuditLogs returns a paginated list of audit log entries.
func (h *AuditHandler) ListAuditLogs(w http.ResponseWriter, r *http.Request) {
	params := db.AuditLogListParams{
		PaginationParams: parsePagination(r),
		Admin:            r.URL.Query().Get("admin"),
		Action:           r.URL.Query().Get("action"),
		TargetType:       r.URL.Query().Get("targetType"),
		Search:           r.URL.Query().Get("search"),
		From:             r.URL.Query().Get("from"),
		To:               r.URL.Query().Get("to"),
	}

	result, err := h.queries.GetAuditLogs(r.Context(), params)
	if err != nil {
		slog.Error("audit logs error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch audit logs"})
		return
	}

	writeJSON(w, http.StatusOK, result)
}
