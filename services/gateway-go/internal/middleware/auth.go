package middleware

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"regexp"
	"strings"

	"github.com/golang-jwt/jwt/v5"
)

// solanaAddressRe matches a valid base58 Solana public key (32–44 chars, no 0/O/I/l).
var solanaAddressRe = regexp.MustCompile(`^[1-9A-HJ-NP-Za-km-z]{32,44}$`)

// isValidSolanaAddress returns true if addr looks like a valid Solana public key.
func isValidSolanaAddress(addr string) bool {
	return solanaAddressRe.MatchString(addr)
}

type contextKey string

const (
	// WalletKey is the context key for the authenticated user's wallet address.
	WalletKey contextKey = "wallet"
)

// cookieName is the HttpOnly cookie set by the auth service on successful login.
const cookieName = "oprai-auth-token"

// JWTAuth creates middleware that validates JWT tokens.
//
// Token resolution order:
//  1. Authorization: Bearer <token> header (e.g. legacy clients, mobile apps)
//  2. oprai-auth-token HttpOnly cookie (browser clients — XSS-safe)
//
// Behaviour:
//   - No token found → request passes through unauthenticated (public routes allowed).
//   - Token present but invalid/expired → 401 Unauthorized (RFC 6750).
//   - Token jti present in blocklist → 401 Unauthorized (logged-out token).
//   - Token valid → wallet injected into context + X-User-Wallet header set.
//
// previousSecret is used for rolling JWT-secret rotation: if the current
// secret fails signature verification AND the failure was a signature
// mismatch (not expiry / malformed), the validator retries against the
// previous secret. This lets tokens issued before a rotation continue to
// work for the rest of their natural TTL. Empty when no rotation is active.
func JWTAuth(jwtSecret, previousSecret string, blocklist *TokenBlocklist) func(http.Handler) http.Handler {
	secret := []byte(jwtSecret)
	var prevSecret []byte
	if previousSecret != "" {
		prevSecret = []byte(previousSecret)
	}

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// 1. Try Authorization: Bearer header first
			var tokenString string
			authHeader := r.Header.Get("Authorization")
			if strings.HasPrefix(authHeader, "Bearer ") {
				tokenString = strings.TrimPrefix(authHeader, "Bearer ")
			}

			// 2. Fall back to HttpOnly cookie
			if tokenString == "" {
				if cookie, err := r.Cookie(cookieName); err == nil {
					tokenString = cookie.Value
				}
			}

			if tokenString == "" {
				// No token supplied — allow unauthenticated access (public endpoints).
				next.ServeHTTP(w, r)
				return
			}
			parseWith := func(s []byte) (*jwt.Token, error) {
				return jwt.Parse(tokenString, func(token *jwt.Token) (interface{}, error) {
					if _, ok := token.Method.(*jwt.SigningMethodHMAC); !ok {
						return nil, jwt.ErrSignatureInvalid
					}
					return s, nil
				})
			}
			token, err := parseWith(secret)
			if err != nil && len(prevSecret) > 0 && strings.Contains(err.Error(), "signature is invalid") {
				token, err = parseWith(prevSecret)
			}

			if err != nil || !token.Valid {
				// Token was supplied but is invalid or expired — reject per RFC 6750.
				slog.Warn("JWT authentication failed",
					"reason", "invalid_or_expired_token",
					"path", r.URL.Path,
					"ip", extractIP(r, false),
				)
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("WWW-Authenticate", `Bearer error="invalid_token", error_description="Token is expired or invalid"`)
				w.WriteHeader(http.StatusUnauthorized)
				_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid or expired token"})
				return
			}

			claims, ok := token.Claims.(jwt.MapClaims)
			if !ok {
				slog.Warn("JWT authentication failed",
					"reason", "malformed_claims",
					"path", r.URL.Path,
					"ip", extractIP(r, false),
				)
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("WWW-Authenticate", `Bearer error="invalid_token", error_description="Malformed token claims"`)
				w.WriteHeader(http.StatusUnauthorized)
				_ = json.NewEncoder(w).Encode(map[string]string{"error": "malformed token claims"})
				return
			}

			// Check token revocation via jti. Tokens are added to the blocklist
			// when the user calls POST /auth/logout so that Bearer copies are
			// invalidated immediately (not just when the cookie is cleared).
			if jti, _ := claims["jti"].(string); blocklist.IsRevoked(jti) {
				slog.Warn("JWT authentication failed",
					"reason", "token_revoked",
					"path", r.URL.Path,
					"ip", extractIP(r, false),
				)
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("WWW-Authenticate", `Bearer error="invalid_token", error_description="Token has been revoked"`)
				w.WriteHeader(http.StatusUnauthorized)
				_ = json.NewEncoder(w).Encode(map[string]string{"error": "token has been revoked"})
				return
			}

			// Extract wallet from "w" claim (as per auth-service JWT format)
			wallet, _ := claims["w"].(string)
			if wallet != "" && isValidSolanaAddress(wallet) {
				ctx := context.WithValue(r.Context(), WalletKey, wallet)
				r = r.WithContext(ctx)
				// Inject trusted header for downstream services
				r.Header.Set("X-User-Wallet", wallet)
			}

			next.ServeHTTP(w, r)
		})
	}
}

// GetWallet extracts the wallet address from the request context.
func GetWallet(ctx context.Context) string {
	if v, ok := ctx.Value(WalletKey).(string); ok {
		return v
	}
	return ""
}

// RequireWallet is middleware that enforces a valid authenticated wallet.
// It relies on JWTAuth having already run: if X-User-Wallet is not set in the
// request (meaning JWTAuth either found no token or an invalid one), it returns
// 401 immediately. Use this on routes that must never be publicly accessible.
func RequireWallet(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if GetWallet(r.Context()) == "" {
			slog.Warn("Unauthenticated access to protected route",
				"path", r.URL.Path,
				"method", r.Method,
				"ip", extractIP(r, false),
			)
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("WWW-Authenticate", `Bearer realm="oprai"`)
			w.WriteHeader(http.StatusUnauthorized)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "authentication required"})
			return
		}
		next.ServeHTTP(w, r)
	})
}
