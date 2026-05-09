package handlers

import (
	"fmt"
	"log/slog"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// ExportHandler handles data export (CSV) endpoints.
type ExportHandler struct {
	queries *db.Queries
}

// NewExportHandler creates a new ExportHandler.
func NewExportHandler(queries *db.Queries) *ExportHandler {
	return &ExportHandler{queries: queries}
}

// ExportUsers exports all users as CSV.
func (h *ExportHandler) ExportUsers(w http.ResponseWriter, r *http.Request) {
	params := db.DateRangeParams{
		From: r.URL.Query().Get("from"),
		To:   r.URL.Query().Get("to"),
	}

	rows, err := h.queries.ExportUsers(r.Context(), params)
	if err != nil {
		slog.Error("export users error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to export users"})
		return
	}

	filename := fmt.Sprintf("oprai-users-%s.csv", time.Now().Format("2006-01-02"))
	sendCSV(w, filename, rows)
}

// ExportTransactions exports all transactions as CSV.
func (h *ExportHandler) ExportTransactions(w http.ResponseWriter, r *http.Request) {
	params := db.DateRangeParams{
		From: r.URL.Query().Get("from"),
		To:   r.URL.Query().Get("to"),
	}

	rows, err := h.queries.ExportTransactions(r.Context(), params)
	if err != nil {
		slog.Error("export transactions error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to export transactions"})
		return
	}

	filename := fmt.Sprintf("oprai-transactions-%s.csv", time.Now().Format("2006-01-02"))
	sendCSV(w, filename, rows)
}

// ExportSessions exports all sessions as CSV.
func (h *ExportHandler) ExportSessions(w http.ResponseWriter, r *http.Request) {
	params := db.DateRangeParams{
		From: r.URL.Query().Get("from"),
		To:   r.URL.Query().Get("to"),
	}

	rows, err := h.queries.ExportSessions(r.Context(), params)
	if err != nil {
		slog.Error("export sessions error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to export sessions"})
		return
	}

	filename := fmt.Sprintf("oprai-sessions-%s.csv", time.Now().Format("2006-01-02"))
	sendCSV(w, filename, rows)
}

// ExportAuditLogs exports admin audit log entries as CSV.
func (h *ExportHandler) ExportAuditLogs(w http.ResponseWriter, r *http.Request) {
	params := db.DateRangeParams{
		From: r.URL.Query().Get("from"),
		To:   r.URL.Query().Get("to"),
	}

	rows, err := h.queries.ExportAuditLogs(r.Context(), params)
	if err != nil {
		slog.Error("export audit logs error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to export audit logs"})
		return
	}

	filename := fmt.Sprintf("oprai-audit-logs-%s.csv", time.Now().Format("2006-01-02"))
	sendCSV(w, filename, rows)
}

func sendCSV(w http.ResponseWriter, filename string, rows []map[string]interface{}) {
	w.Header().Set("Content-Type", "text/csv; charset=utf-8")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filename))
	// BOM for Excel compatibility
	w.Write([]byte("\xEF\xBB\xBF"))
	w.Write([]byte(toCsv(rows)))
}

func toCsv(rows []map[string]interface{}) string {
	if len(rows) == 0 {
		return ""
	}

	// Deterministic header order: sort keys alphabetically.
	headers := make([]string, 0, len(rows[0]))
	for k := range rows[0] {
		headers = append(headers, k)
	}
	sort.Strings(headers)

	var sb strings.Builder
	// Write header row
	for i, h := range headers {
		if i > 0 {
			sb.WriteByte(',')
		}
		sb.WriteString(csvEscape(h))
	}
	sb.WriteByte('\n')

	for _, row := range rows {
		for i, h := range headers {
			if i > 0 {
				sb.WriteByte(',')
			}
			v := row[h]
			if v == nil {
				continue
			}
			sb.WriteString(csvEscape(fmt.Sprintf("%v", v)))
		}
		sb.WriteByte('\n')
	}

	return sb.String()
}

func csvEscape(s string) string {
	if strings.ContainsAny(s, ",\"\n\r") {
		return `"` + strings.ReplaceAll(s, `"`, `""`) + `"`
	}
	return s
}
