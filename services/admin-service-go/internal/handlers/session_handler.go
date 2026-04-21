package handlers

import (
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// SessionHandler handles session listing endpoints.
type SessionHandler struct {
	queries *db.Queries
}

// NewSessionHandler creates a new SessionHandler.
func NewSessionHandler(queries *db.Queries) *SessionHandler {
	return &SessionHandler{queries: queries}
}

// ListSessions returns a paginated list of chat sessions.
func (h *SessionHandler) ListSessions(w http.ResponseWriter, r *http.Request) {
	params := db.SessionListParams{
		PaginationParams: parsePagination(r),
		Search:           r.URL.Query().Get("search"),
		From:             r.URL.Query().Get("from"),
		To:               r.URL.Query().Get("to"),
	}

	result, err := h.queries.GetSessions(r.Context(), params)
	if err != nil {
		slog.Error("sessions list error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch sessions"})
		return
	}

	writeJSON(w, http.StatusOK, result)
}

// GetSessionMessages returns all messages for a specific session.
func (h *SessionHandler) GetSessionMessages(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")

	messages, err := h.queries.GetSessionMessages(r.Context(), id)
	if err != nil {
		slog.Error("session messages error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch session messages"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{"data": messages})
}

// CloseSession closes an active session.
func (h *SessionHandler) CloseSession(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")

	if err := h.queries.CloseSession(r.Context(), id); err != nil {
		slog.Error("close session error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to close session"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true})
}
