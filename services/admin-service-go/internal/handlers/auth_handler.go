package handlers

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"golang.org/x/crypto/bcrypt"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
	"github.com/oprai/oprai/services/admin-service-go/internal/middleware"
	"github.com/oprai/oprai/services/admin-service-go/internal/services"
)

// adminCookieName is the HttpOnly session cookie for the admin panel.
const adminCookieName = "oprai-admin-token"

// AuthHandler handles admin authentication endpoints.
type AuthHandler struct {
	authService *services.AdminAuth
	queries     *db.Queries
	isProd      bool
	cookieTTL   int // seconds
}

// NewAuthHandler creates a new AuthHandler.
func NewAuthHandler(authService *services.AdminAuth, queries *db.Queries, isProd bool, cookieTTL int) *AuthHandler {
	return &AuthHandler{
		authService: authService,
		queries:     queries,
		isProd:      isProd,
		cookieTTL:   cookieTTL,
	}
}

// setAdminCookie sets the oprai-admin-token HttpOnly cookie.
func (h *AuthHandler) setAdminCookie(w http.ResponseWriter, token string) {
	http.SetCookie(w, &http.Cookie{
		Name:     adminCookieName,
		Value:    token,
		Path:     "/",
		MaxAge:   h.cookieTTL,
		HttpOnly: true,
		Secure:   h.isProd,
		SameSite: http.SameSiteStrictMode,
	})
}

// clearAdminCookie expires the oprai-admin-token cookie.
func (h *AuthHandler) clearAdminCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{
		Name:     adminCookieName,
		Value:    "",
		Path:     "/",
		MaxAge:   -1,
		HttpOnly: true,
		Secure:   h.isProd,
		SameSite: http.SameSiteStrictMode,
	})
}

type loginRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

// Login authenticates an admin user and returns a JWT token.
func (h *AuthHandler) Login(w http.ResponseWriter, r *http.Request) {
	var req loginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Invalid request body"})
		return
	}

	if req.Username == "" || req.Password == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Username and password are required"})
		return
	}

	ip := extractIP(r)

	// ── Brute-force guard ────────────────────────────────────────────────────
	if blocked, reason, retryAfter := checkLogin(ip, req.Username); blocked {
		slog.Warn("admin login blocked (brute-force guard)", "username", req.Username, "ip", ip)
		w.Header().Set("Retry-After", fmt.Sprintf("%d", retryAfter))
		writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": reason})
		return
	}

	admin, err := h.queries.GetAdminByUsername(r.Context(), req.Username)
	if err != nil {
		// Record failure even for unknown usernames to prevent user enumeration
		// via timing differences.
		msg := recordFailure(ip, req.Username)
		slog.Warn("admin login failed (unknown user)", "username", req.Username, "ip", ip)
		go func(username, ipAddr, ua string) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if err := h.queries.InsertFailedLoginAttempt(
				ctx, username, ipAddr, ua, "unknown user",
			); err != nil {
				slog.Error("failed to record login attempt", "error", err)
			}
		}(req.Username, ip, r.UserAgent())
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": msg})
		return
	}

	if err := bcrypt.CompareHashAndPassword([]byte(admin.PasswordHash), []byte(req.Password)); err != nil {
		msg := recordFailure(ip, req.Username)
		slog.Warn("admin login failed (wrong password)", "username", req.Username, "ip", ip)
		// Record failed attempt to DB (non-blocking)
		go func(username, ipAddr, ua string) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if err := h.queries.InsertFailedLoginAttempt(
				ctx, username, ipAddr, ua, "wrong password",
			); err != nil {
				slog.Error("failed to record login attempt", "error", err)
			}
		}(req.Username, ip, r.UserAgent())
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": msg})
		return
	}

	// Successful login — clear failure counters.
	recordSuccess(ip, req.Username)

	token, expiresAt, err := h.authService.CreateToken(admin.Username, admin.Role)
	if err != nil {
		slog.Error("failed to create admin token", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to create token"})
		return
	}

	slog.Info("admin login successful", "username", req.Username, "ip", ip)

	// Set HttpOnly cookie so the admin panel doesn't need to store the token in localStorage.
	h.setAdminCookie(w, token)

	// Token hash stored for session lookup (SHA-256, not reversible).
	tokenHash := fmt.Sprintf("%x", sha256.Sum256([]byte(token)))
	userAgent := r.UserAgent()

	// Audit log + last_login_at + session creation (fire-and-forget).
	// Uses a detached context with a timeout so these writes are not cancelled
	// when the HTTP response is sent and r.Context() is torn down.
	go func(adminID, adminUsername, adminIP, adminUA, adminTokenHash string) {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := h.queries.UpdateLastLogin(ctx, adminID); err != nil {
			slog.Error("failed to update last_login_at", "error", err)
		}
		if err := h.queries.CreateAuditLog(ctx, db.AuditLogEntry{
			AdminID:       adminID,
			AdminUsername: adminUsername,
			Action:        "admin.login",
			IPAddress:     adminIP,
		}); err != nil {
			slog.Error("audit log error", "error", err)
		}
		if _, err := h.queries.CreateAdminSession(ctx, adminID, adminUsername, adminIP, adminUA, adminTokenHash); err != nil {
			slog.Error("failed to create admin session", "error", err)
		}
	}(admin.ID, admin.Username, ip, userAgent, tokenHash)

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"ok":               true,
		"token":            token,
		"expiresAt":        expiresAt,
		"username":         admin.Username,
		"mustChangePassword": admin.MustChangePassword,
	})
}

// Logout clears the admin session cookie.
func (h *AuthHandler) Logout(w http.ResponseWriter, r *http.Request) {
	h.clearAdminCookie(w)
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
}

// Verify checks if the admin token is still valid.
func (h *AuthHandler) Verify(w http.ResponseWriter, r *http.Request) {
	username := middleware.GetAdminUsername(r.Context())
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"ok":       true,
		"username": username,
	})
}

// ListAdmins returns all admin users.
func (h *AuthHandler) ListAdmins(w http.ResponseWriter, r *http.Request) {
	admins, err := h.queries.ListAdminUsers(r.Context())
	if err != nil {
		slog.Error("list admin users error", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to list admin users"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"data": admins})
}

type createAdminRequest struct {
	Username string `json:"username"`
}

// CreateAdmin creates a new admin user with a generated password.
func (h *AuthHandler) CreateAdmin(w http.ResponseWriter, r *http.Request) {
	var req createAdminRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Invalid request body"})
		return
	}

	if len(req.Username) < 3 || len(req.Username) > 50 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Username must be 3-50 characters"})
		return
	}

	// Check if username already exists
	existing, _ := h.queries.GetAdminByUsername(r.Context(), req.Username)
	if existing != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "Admin user already exists"})
		return
	}

	password, err := generatePassword()
	if err != nil {
		slog.Error("failed to generate password", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to create admin user"})
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(password), 12)
	if err != nil {
		slog.Error("failed to hash password", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to create admin user"})
		return
	}

	admin, err := h.queries.CreateAdminUser(r.Context(), req.Username, string(hash))
	if err != nil {
		slog.Error("failed to create admin user", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to create admin user"})
		return
	}

	writeJSON(w, http.StatusCreated, map[string]interface{}{
		"ok": true,
		"admin": map[string]interface{}{
			"id":       admin.ID,
			"username": admin.Username,
			"role":     admin.Role,
		},
		"generatedPassword": password,
		"passwordNote":      "Shown once — store it securely. The user must change it on first login.",
	})
}

// DeleteAdmin removes an admin user.
func (h *AuthHandler) DeleteAdmin(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	currentUsername := middleware.GetAdminUsername(r.Context())

	admin, err := h.queries.GetAdminByID(r.Context(), id)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "Admin user not found"})
		return
	}

	// Prevent self-deletion
	if admin.Username == currentUsername {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Cannot delete your own account"})
		return
	}

	if err := h.queries.DeleteAdminUser(r.Context(), id); err != nil {
		slog.Error("failed to delete admin user", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to delete admin user"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true})
}

// ResetPassword resets an admin user's password.
func (h *AuthHandler) ResetPassword(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")

	password, err := generatePassword()
	if err != nil {
		slog.Error("failed to generate password", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to reset password"})
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(password), 12)
	if err != nil {
		slog.Error("failed to hash password", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to reset password"})
		return
	}

	// forceChange=true so the user must set a new password on next login
	if err := h.queries.UpdateAdminPassword(r.Context(), id, string(hash), true); err != nil {
		slog.Error("failed to update admin password", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "Failed to reset password"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"ok":                true,
		"generatedPassword": password,
		"passwordNote":      "Shown once — store it securely. The user must change it on next login.",
	})
}

func generatePassword() (string, error) {
	b := make([]byte, 20) // 160 bits of entropy — well above the 128-bit minimum
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("failed to generate random bytes: %w", err)
	}
	s := base64.URLEncoding.EncodeToString(b)
	if len(s) > 20 {
		s = s[:20]
	}
	return s, nil
}

// extractIP returns the client IP, always using the raw TCP address to prevent
// spoofing via X-Forwarded-For headers. The admin panel does not run behind a
// reverse proxy in the typical deployment.
func extractIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		// r.RemoteAddr may lack a port in some test/mock scenarios.
		return strings.TrimSpace(r.RemoteAddr)
	}
	return host
}
