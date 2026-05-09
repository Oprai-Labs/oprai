package handlers

import (
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// TransactionHandler handles transaction listing endpoints.
type TransactionHandler struct {
	queries *db.Queries
}

// NewTransactionHandler creates a new TransactionHandler.
func NewTransactionHandler(queries *db.Queries) *TransactionHandler {
	return &TransactionHandler{queries: queries}
}

// ListTransactions returns a paginated list of transactions with optional filters.
func (h *TransactionHandler) ListTransactions(w http.ResponseWriter, r *http.Request) {
	params := db.TransactionListParams{
		PaginationParams: parsePagination(r),
		Status:           r.URL.Query().Get("status"),
		Action:           r.URL.Query().Get("action"),
		Protocol:         r.URL.Query().Get("protocol"),
		Search:           r.URL.Query().Get("search"),
		From:             r.URL.Query().Get("from"),
		To:               r.URL.Query().Get("to"),
	}

	result, err := h.queries.GetTransactions(r.Context(), params)
	if err != nil {
		slog.Error("transactions list error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch transactions"})
		return
	}

	writeJSON(w, http.StatusOK, result)
}

// GetTransaction returns details for a single transaction.
func (h *TransactionHandler) GetTransaction(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")

	tx, err := h.queries.GetTransactionByID(r.Context(), id)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "Transaction not found"})
		return
	}

	writeJSON(w, http.StatusOK, tx)
}
