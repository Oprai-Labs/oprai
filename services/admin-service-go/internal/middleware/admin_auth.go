package middleware

import (
	"context"
	"log/slog"
	"net/http"
	"strings"

	"github.com/oprai/oprai/services/admin-service-go/internal/services"
)

type contextKey string

const (
	// AdminUsernameKey is the context key for the authenticated admin username.
	AdminUsernameKey contextKey = "adminUsername"
	// AdminRoleKey is the context key for the authenticated admin role.
	AdminRoleKey contextKey = "adminRole"
)

// Admin roles — must match admin_schema.admin_users.role check constraint.
const (
	RoleSuperAdmin = "superadmin"
	RoleAdmin      = "admin"
	RoleViewer     = "viewer"
)

// AdminAuth provides JWT authentication middleware for admin routes.
type AdminAuth struct {
	authService *services.AdminAuth
}

// NewAdminAuth creates a new AdminAuth middleware.
func NewAdminAuth(authService *services.AdminAuth) *AdminAuth {
	return &AdminAuth{authService: authService}
}

// adminCookieName matches the cookie set by the Login handler.
const adminCookieName = "oprai-admin-token"

// Middleware validates the admin JWT from the Authorization header or HttpOnly cookie.
// On success it stores both Username and Role in the request context.
func (a *AdminAuth) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Resolve token: Bearer header first, then cookie fallback.
		var token string
		if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
			token = strings.TrimPrefix(authHeader, "Bearer ")
		} else if cookie, err := r.Cookie(adminCookieName); err == nil {
			token = cookie.Value
		}

		if token == "" {
			slog.Warn("Admin authentication rejected: no token provided",
				"path", r.URL.Path,
				"method", r.Method,
			)
			writeJSONError(w, http.StatusUnauthorized, "Admin authentication required")
			return
		}
		claims, err := a.authService.VerifyToken(token)
		if err != nil {
			slog.Warn("Admin authentication rejected: invalid or expired token",
				"path", r.URL.Path,
			)
			writeJSONError(w, http.StatusUnauthorized, "Invalid or expired admin token")
			return
		}

		ctx := context.WithValue(r.Context(), AdminUsernameKey, claims.Username)
		ctx = context.WithValue(ctx, AdminRoleKey, claims.Role)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// GetAdminUsername extracts the admin username from the request context.
func GetAdminUsername(ctx context.Context) string {
	if v, ok := ctx.Value(AdminUsernameKey).(string); ok {
		return v
	}
	return ""
}

// GetAdminRole extracts the admin role from the request context.
func GetAdminRole(ctx context.Context) string {
	if v, ok := ctx.Value(AdminRoleKey).(string); ok {
		return v
	}
	return ""
}

// RequireSuperAdmin enforces that only superadmin-role tokens can access the route.
// Must be used after Middleware (which populates the role in context).
func RequireSuperAdmin(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		role := GetAdminRole(r.Context())
		if role != RoleSuperAdmin {
			slog.Warn("Admin authorization rejected: superadmin role required",
				"role", role,
				"path", r.URL.Path,
				"username", GetAdminUsername(r.Context()),
			)
			writeJSONError(w, http.StatusForbidden, "Superadmin role required")
			return
		}
		next.ServeHTTP(w, r)
	})
}

// RequireAdmin enforces that only admin or superadmin-role tokens can access the route.
// Must be used after Middleware (which populates the role in context).
func RequireAdmin(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		role := GetAdminRole(r.Context())
		if role != RoleAdmin && role != RoleSuperAdmin {
			slog.Warn("Admin authorization rejected: admin role required",
				"role", role,
				"path", r.URL.Path,
				"username", GetAdminUsername(r.Context()),
			)
			writeJSONError(w, http.StatusForbidden, "Admin role required")
			return
		}
		next.ServeHTTP(w, r)
	})
}
