package handlers

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
	"github.com/oprai/oprai/services/admin-service-go/internal/middleware"
)

// UserHandler handles user management endpoints.
type UserHandler struct {
	queries *db.Queries
}

// NewUserHandler creates a new UserHandler.
func NewUserHandler(queries *db.Queries) *UserHandler {
	return &UserHandler{queries: queries}
}

// ListUsers returns a paginated list of users with optional search and filters.
func (h *UserHandler) ListUsers(w http.ResponseWriter, r *http.Request) {
	params := db.UserListParams{
		PaginationParams: parsePagination(r),
		Search:           r.URL.Query().Get("search"),
		Sort:             r.URL.Query().Get("sort"),
		Order:            r.URL.Query().Get("order"),
		Status:           r.URL.Query().Get("status"),
		Role:             r.URL.Query().Get("role"),
		From:             r.URL.Query().Get("from"),
		To:               r.URL.Query().Get("to"),
	}

	result, err := h.queries.GetUsers(r.Context(), params)
	if err != nil {
		slog.Error("users list error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch users"})
		return
	}

	writeJSON(w, http.StatusOK, result)
}

// GetUser returns details for a single user.
func (h *UserHandler) GetUser(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")

	user, err := h.queries.GetUserByID(r.Context(), id)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "User not found"})
		return
	}

	writeJSON(w, http.StatusOK, user)
}

// GetUserOverview returns a user with their recent sessions and transactions.
func (h *UserHandler) GetUserOverview(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")

	overview, err := h.queries.GetUserOverview(r.Context(), id)
	if err != nil {
		slog.Error("user overview error", "error", err)
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "User not found"})
		return
	}

	writeJSON(w, http.StatusOK, overview)
}

// GetUserSessions returns paginated sessions for a specific user.
func (h *UserHandler) GetUserSessions(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	params := db.DateRangeParams{
		PaginationParams: parsePagination(r),
		From:             r.URL.Query().Get("from"),
		To:               r.URL.Query().Get("to"),
	}

	result, err := h.queries.GetUserSessions(r.Context(), id, params)
	if err != nil {
		slog.Error("user sessions error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch user sessions"})
		return
	}

	writeJSON(w, http.StatusOK, result)
}

// GetUserTransactions returns paginated transactions for a specific user.
func (h *UserHandler) GetUserTransactions(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	params := db.DateRangeParams{
		PaginationParams: parsePagination(r),
		From:             r.URL.Query().Get("from"),
		To:               r.URL.Query().Get("to"),
	}

	result, err := h.queries.GetUserTransactions(r.Context(), id, params)
	if err != nil {
		slog.Error("user transactions error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch user transactions"})
		return
	}

	writeJSON(w, http.StatusOK, result)
}

// GetUserIPLogs returns paginated IP logs for a specific user (by wallet address).
func (h *UserHandler) GetUserIPLogs(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")

	// First resolve user_id → wallet_address
	user, err := h.queries.GetUserByID(r.Context(), id)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "User not found"})
		return
	}

	wallet := user.WalletAddress

	params := db.IPLogListParams{
		PaginationParams: parsePagination(r),
		Wallet:           wallet,
		From:             r.URL.Query().Get("from"),
		To:               r.URL.Query().Get("to"),
	}

	result, err := h.queries.ListIPLogs(r.Context(), params)
	if err != nil {
		slog.Error("user ip logs error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch IP logs"})
		return
	}

	writeJSON(w, http.StatusOK, result)
}

// GetUserAuditLogs returns audit logs targeting a specific user.
func (h *UserHandler) GetUserAuditLogs(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")

	logs, err := h.queries.GetAuditLogsForTarget(r.Context(), "user", id)
	if err != nil {
		slog.Error("user audit logs error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to fetch user audit logs"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{"data": logs})
}

type updateStatusRequest struct {
	Status string `json:"status"`
	Reason string `json:"reason"`
}

// UpdateUserStatus updates a user's status (active/suspended/banned).
func (h *UserHandler) UpdateUserStatus(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	adminUsername := middleware.GetAdminUsername(r.Context())

	var req updateStatusRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Invalid request body"})
		return
	}

	validStatuses := map[string]bool{"active": true, "suspended": true, "banned": true}
	if !validStatuses[req.Status] {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Status must be active, suspended, or banned"})
		return
	}

	user, err := h.queries.UpdateUserStatus(r.Context(), id, req.Status, req.Reason, adminUsername)
	if err != nil {
		slog.Error("user status update error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to update user status"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{"data": user})
}

type updateRoleRequest struct {
	Role string `json:"role"`
}

// UpdateUserRole updates a user's role.
func (h *UserHandler) UpdateUserRole(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")

	var req updateRoleRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Invalid request body"})
		return
	}

	validRoles := map[string]bool{"user": true, "admin": true, "superadmin": true}
	if req.Role == "" || !validRoles[req.Role] {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Invalid role: must be user, admin, or superadmin"})
		return
	}

	user, err := h.queries.UpdateUserRole(r.Context(), id, req.Role)
	if err != nil {
		slog.Error("user role update error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to update user role"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{"data": user})
}
