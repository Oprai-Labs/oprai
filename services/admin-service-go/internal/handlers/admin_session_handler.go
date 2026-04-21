package handlers

import (
	"log/slog"
	"net/http"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// AdminSessionHandler handles admin session management endpoints.
type AdminSessionHandler struct {
	queries *db.Queries
}

// NewAdminSessionHandler creates a new AdminSessionHandler.
func NewAdminSessionHandler(queries *db.Queries) *AdminSessionHandler {
	return &AdminSessionHandler{queries: queries}
}

// ListAdminSessions returns paginated admin login sessions.
func (h *AdminSessionHandler) ListAdminSessions(w http.ResponseWriter, r *http.Request) {
	params := db.AdminSessionListParams{
		PaginationParams: parsePagination(r),
		AdminUsername:    r.URL.Query().Get("username"),
		Active:           r.URL.Query().Get("active"),
		From:             r.URL.Query().Get("from"),
		To:               r.URL.Query().Get("to"),
	}

	result, err := h.queries.ListAdminSessions(r.Context(), params)
	if err != nil {
		slog.Error("admin sessions list error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch admin sessions"})
		return
	}

	writeJSON(w, http.StatusOK, result)
}
