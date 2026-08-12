package handlers

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log/slog"
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

// decoyBcryptHash is compared against on the unknown-username login path so that
// a login for a non-existent user costs the same bcrypt work as one for a real
// user with a wrong password. Without it, the unknown-user path returned
// immediately (no bcrypt) while the wrong-password path spent ~cost-12 bcrypt
// time — a timing side channel that reveals which admin usernames exist. Cost
// must match the real password hashes (12).
var decoyBcryptHash, _ = bcrypt.GenerateFromPassword(
	[]byte("constant-time-login-decoy-value"), 12)

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
		// Spend the same bcrypt time as the wrong-password path below so an
		// unknown username is indistinguishable from a known one by response
		// latency (closes the user-enumeration timing side channel).
		_ = bcrypt.CompareHashAndPassword(decoyBcryptHash, []byte(req.Password))
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

// Logout clears the admin session cookie AND revokes the presented token, so a
// captured copy cannot be reused before its natural expiry. Revoking for the
// full cookie TTL from now always covers the token's remaining life.
func (h *AuthHandler) Logout(w http.ResponseWriter, r *http.Request) {
	if token := adminTokenFromRequest(r); token != "" {
		middleware.RevokeAdminToken(token, time.Now().Add(time.Duration(h.cookieTTL)*time.Second))
	}
	h.clearAdminCookie(w)
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
}

// adminTokenFromRequest resolves the admin token from the Authorization bearer
// header or the HttpOnly cookie — same precedence as the auth middleware.
func adminTokenFromRequest(r *http.Request) string {
	if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
		return strings.TrimPrefix(authHeader, "Bearer ")
	}
	if cookie, err := r.Cookie(adminCookieName); err == nil {
		return cookie.Value
	}
	return ""
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

// extractIP returns the real client IP via the shared proxy-aware helper. The
// admin service sits behind Caddy (which overwrites X-Real-IP with the true
// peer) and is not directly reachable, so the per-IP brute-force guard now
// tracks real client IPs instead of the proxy's container address — which had
// collapsed every caller into one bucket (a global-lockout DoS with no real
// per-IP tracking).
func extractIP(r *http.Request) string {
	return middleware.ClientIP(r)
}
