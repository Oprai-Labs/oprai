package handlers

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// writeJSON writes a JSON response with the given status code.
func writeJSON(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if data != nil {
		json.NewEncoder(w).Encode(data)
	}
}

// parsePagination extracts page and limit from query parameters with defaults.
func parsePagination(r *http.Request) db.PaginationParams {
	page := 1
	limit := 20

	if p := r.URL.Query().Get("page"); p != "" {
		if v, err := strconv.Atoi(p); err == nil && v >= 1 {
			page = v
		}
	}
	if l := r.URL.Query().Get("limit"); l != "" {
		if v, err := strconv.Atoi(l); err == nil && v >= 1 && v <= 100 {
			limit = v
		}
	}

	return db.PaginationParams{Page: page, Limit: limit}
}
