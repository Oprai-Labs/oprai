package handlers

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// IssueHandler serves the user issue-report queue (Help → Report Issue).
type IssueHandler struct {
	queries *db.Queries
}

// NewIssueHandler creates a new IssueHandler.
func NewIssueHandler(queries *db.Queries) *IssueHandler {
	return &IssueHandler{queries: queries}
}

// ListIssueReports returns a paginated list of reports, open ones first.
func (h *IssueHandler) ListIssueReports(w http.ResponseWriter, r *http.Request) {
	params := db.IssueReportListParams{
		PaginationParams: parsePagination(r),
		Status:           r.URL.Query().Get("status"),
		Category:         r.URL.Query().Get("category"),
		Search:           r.URL.Query().Get("search"),
	}

	result, err := h.queries.GetIssueReports(r.Context(), params)
	if err != nil {
		slog.Error("issue reports list error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch issue reports"})
		return
	}

	writeJSON(w, http.StatusOK, result)
}

type updateIssueRequest struct {
	Status string `json:"status"`
	Note   string `json:"note"`
}

// UpdateIssueReport sets a report's status and optionally appends a note.
func (h *IssueHandler) UpdateIssueReport(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")

	var body updateIssueRequest
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Invalid request body"})
		return
	}

	if err := h.queries.UpdateIssueReport(r.Context(), id, body.Status, body.Note); err != nil {
		// An unknown status is the caller's mistake, not a server fault — the
		// query layer validates it against the same set the table's CHECK
		// enforces, so surface it as a 400 rather than a 500.
		slog.Warn("update issue report rejected", "error", err, "id", id, "status", body.Status)
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Could not update the report"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
}
