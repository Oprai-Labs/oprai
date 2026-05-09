package middleware

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"

	"github.com/oprai/oprai/services/auth-service-go/internal/services"
)

// contextKey is an unexported type for context keys to prevent collisions.
type contextKey string

const (
	// WalletContextKey is the context key for the authenticated wallet address.
	WalletContextKey contextKey = "wallet"
)

// WalletFromContext extracts the wallet address from the request context.
// Returns empty string if not present.
func WalletFromContext(ctx context.Context) string {
	val, _ := ctx.Value(WalletContextKey).(string)
	return val
}

// Auth creates JWT authentication middleware.
// For protected routes, it validates the Bearer token or gateway-injected headers.
type Auth struct {
	jwtService     *services.JWTService
	internalAPIKey string
}

// NewAuth creates a new Auth middleware instance.
func NewAuth(jwtService *services.JWTService, internalAPIKey string) *Auth {
	return &Auth{
		jwtService:     jwtService,
		internalAPIKey: internalAPIKey,
	}
}

// Required returns middleware that rejects unauthenticated requests with 401.
func (a *Auth) Required(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		wallet := a.extractWallet(r)
		if wallet == "" {
			slog.Warn("Authentication rejected: missing or invalid credentials",
				"path", r.URL.Path,
				"method", r.Method,
			)
			writeJSON(w, http.StatusUnauthorized, map[string]string{
				"error": "Missing or invalid Authorization header",
			})
			return
		}

		ctx := context.WithValue(r.Context(), WalletContextKey, wallet)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}


// extractWallet tries to extract the wallet address from:
// 1. Gateway-injected X-User-Wallet + X-Internal-Api-Key headers
// 2. Authorization: Bearer <jwt> header
func (a *Auth) extractWallet(r *http.Request) string {
	// Check gateway-injected headers first (trusted internal calls)
	gatewayWallet := r.Header.Get("X-User-Wallet")
	if gatewayWallet != "" {
		internalKey := r.Header.Get("X-Internal-Api-Key")
		// Use constant-time comparison to prevent timing-oracle attacks on the
		// internal API key (the Python services already use secrets.compare_digest).
		if a.internalAPIKey != "" && subtle.ConstantTimeCompare([]byte(internalKey), []byte(a.internalAPIKey)) == 1 {
			return gatewayWallet
		}
		// Internal key missing or mismatch -- fall through to JWT validation
	}

	// Fall back to Bearer token validation
	authHeader := r.Header.Get("Authorization")
	if authHeader == "" || !strings.HasPrefix(authHeader, "Bearer ") {
		return ""
	}

	tokenStr := authHeader[7:]
	wallet, _, err := a.jwtService.Validate(tokenStr)
	if err != nil {
		return ""
	}

	return wallet
}

// writeJSON writes a JSON-encoded response with the given status code.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Error("Failed to encode JSON response", "error", err)
	}
}
