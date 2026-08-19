package handlers

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/golang-jwt/jwt/v5"
	"github.com/oprai/oprai/services/gateway-go/internal/middleware"
)

// AuthProxy reverse-proxies REST requests to the auth service's HTTP server.
type AuthProxy struct {
	proxy          *httputil.ReverseProxy
	internalAPIKey string
	blocklist      *middleware.TokenBlocklist
	jwtSecret      []byte
}

// NewAuthProxy creates a new AuthProxy that forwards requests to authServiceURL.
func NewAuthProxy(authServiceURL string, internalAPIKey string, blocklist *middleware.TokenBlocklist, jwtSecret string) *AuthProxy {
	target, err := url.Parse(authServiceURL)
	if err != nil {
		slog.Error("invalid auth service URL", "url", authServiceURL, "error", err)
		// Fall back to default
		target, _ = url.Parse("http://localhost:3010")
	}

	rp := httputil.NewSingleHostReverseProxy(target)

	// Custom director to inject internal headers
	originalDirector := rp.Director
	rp.Director = func(req *http.Request) {
		originalDirector(req)
		req.Header.Del("X-User-Wallet")
		if wallet := middleware.GetWallet(req.Context()); wallet != "" {
			req.Header.Set("X-User-Wallet", wallet)
		}
		req.Header.Set("X-Internal-Api-Key", internalAPIKey)
		if reqID := chimiddleware.GetReqID(req.Context()); reqID != "" {
			req.Header.Set("X-Request-ID", reqID)
		}
	}

	// Strip CORS headers from upstream — the gateway CORS middleware handles them
	rp.ModifyResponse = func(resp *http.Response) error {
		resp.Header.Del("Access-Control-Allow-Origin")
		resp.Header.Del("Access-Control-Allow-Methods")
		resp.Header.Del("Access-Control-Allow-Headers")
		resp.Header.Del("Access-Control-Allow-Credentials")
		resp.Header.Del("Access-Control-Expose-Headers")
		resp.Header.Del("Access-Control-Max-Age")
		return nil
	}

	rp.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		slog.Error("auth proxy error", "error", err, "path", r.URL.Path)
		writeError(w, http.StatusBadGateway, "Auth service unavailable")
	}

	return &AuthProxy{
		proxy:          rp,
		internalAPIKey: internalAPIKey,
		blocklist:      blocklist,
		jwtSecret:      []byte(jwtSecret),
	}
}

// PostNonce proxies POST /auth/nonce to the auth service.
func (p *AuthProxy) PostNonce(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/auth/nonce"
	p.proxy.ServeHTTP(w, r)
}

// PostVerify proxies POST /auth/verify to the auth service.
func (p *AuthProxy) PostVerify(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/auth/verify"
	p.proxy.ServeHTTP(w, r)
}

// GetMe proxies GET /auth/me to the auth service.
func (p *AuthProxy) GetMe(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/auth/me"
	p.proxy.ServeHTTP(w, r)
}

// GetAccountMe proxies GET /account/me to the auth service — the account and its
// linked identities (multichain profile).
func (p *AuthProxy) GetAccountMe(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/account/me"
	p.proxy.ServeHTTP(w, r)
}

// PostAccountLinkNonce proxies POST /account/link/nonce to the auth service.
func (p *AuthProxy) PostAccountLinkNonce(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/account/link/nonce"
	p.proxy.ServeHTTP(w, r)
}

// PostAccountLinkVerify proxies POST /account/link/verify to the auth service.
func (p *AuthProxy) PostAccountLinkVerify(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/account/link/verify"
	p.proxy.ServeHTTP(w, r)
}

// GetUser proxies GET /users/{wallet} to the auth service.
func (p *AuthProxy) GetUser(w http.ResponseWriter, r *http.Request) {
	wallet := chi.URLParam(r, "wallet")
	r.URL.Path = "/users/" + wallet
	p.proxy.ServeHTTP(w, r)
}

// GetSpendingLimits proxies GET /users/me/spending-limits to the auth service.
func (p *AuthProxy) GetSpendingLimits(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/users/me/spending-limits"
	p.proxy.ServeHTTP(w, r)
}

// PutSpendingLimits proxies PUT /users/me/spending-limits to the auth service.
func (p *AuthProxy) PutSpendingLimits(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/users/me/spending-limits"
	p.proxy.ServeHTTP(w, r)
}

// GetSession proxies GET /auth/session to the auth service.
func (p *AuthProxy) GetSession(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/auth/session"
	p.proxy.ServeHTTP(w, r)
}

// PostLogout revokes the caller's JWT in the gateway blocklist, then proxies
// POST /auth/logout to the auth service (which clears the HttpOnly cookie).
func (p *AuthProxy) PostLogout(w http.ResponseWriter, r *http.Request) {
	// Extract token from header or cookie and add its jti to the blocklist so
	// that any in-flight Bearer copies are rejected immediately.
	var tokenStr string
	if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
		tokenStr = strings.TrimPrefix(authHeader, "Bearer ")
	} else if cookie, err := r.Cookie("oprai-auth-token"); err == nil {
		tokenStr = cookie.Value
	}
	if tokenStr != "" && p.blocklist != nil {
		p.revokeToken(tokenStr)
	}

	r.URL.Path = "/auth/logout"
	p.proxy.ServeHTTP(w, r)
}

// revokeToken parses the JWT (signature not verified — we just need the jti)
// and adds the jti to the gateway blocklist.
func (p *AuthProxy) revokeToken(tokenStr string) {
	parser := jwt.NewParser()
	claims := jwt.MapClaims{}
	token, _, err := parser.ParseUnverified(tokenStr, claims)
	if err != nil || token == nil {
		return
	}
	jti, _ := claims["jti"].(string)
	if jti == "" {
		return
	}
	var expiry time.Time
	if expClaim, ok := claims["exp"]; ok {
		switch v := expClaim.(type) {
		case float64:
			expiry = time.Unix(int64(v), 0)
		case json.Number:
			if n, err := v.Int64(); err == nil {
				expiry = time.Unix(n, 0)
			}
		}
	}
	if expiry.IsZero() {
		expiry = time.Now().Add(1 * time.Hour)
	}
	p.blocklist.Revoke(jti, expiry)
}
