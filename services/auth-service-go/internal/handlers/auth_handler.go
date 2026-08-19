package handlers

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/oprai/oprai/services/auth-service-go/internal/audit"
	"github.com/oprai/oprai/services/auth-service-go/internal/config"
	"github.com/oprai/oprai/services/auth-service-go/internal/db"
	"github.com/oprai/oprai/services/auth-service-go/internal/middleware"
	"github.com/oprai/oprai/services/auth-service-go/internal/services"
)

// authCookieName is the HttpOnly cookie used to persist the user JWT.
const authCookieName = "oprai-auth-token"

// AuthHandler handles authentication-related HTTP endpoints.
type AuthHandler struct {
	nonceService      *services.NonceService
	jwtService        *services.JWTService
	revocationService *services.RevocationService
	queries           *db.Queries
	cfg               *config.Config
	auditLogger       *audit.Logger
}

// NewAuthHandler creates a new AuthHandler.
func NewAuthHandler(
	nonceService *services.NonceService,
	jwtService *services.JWTService,
	revocationService *services.RevocationService,
	queries *db.Queries,
	cfg *config.Config,
	auditLogger *audit.Logger,
) *AuthHandler {
	return &AuthHandler{
		nonceService:      nonceService,
		jwtService:        jwtService,
		revocationService: revocationService,
		queries:           queries,
		cfg:               cfg,
		auditLogger:       auditLogger,
	}
}

// setAuthCookie sets the oprai-auth-token HttpOnly cookie on the response.
func (h *AuthHandler) setAuthCookie(w http.ResponseWriter, token string) {
	http.SetCookie(w, &http.Cookie{
		Name:     authCookieName,
		Value:    token,
		Path:     "/",
		MaxAge:   h.cfg.SessionTTLSeconds,
		HttpOnly: true,
		Secure:   h.cfg.IsProd(), // Secure in production (HTTPS only)
		SameSite: http.SameSiteStrictMode,
	})
}

// clearAuthCookie removes the oprai-auth-token cookie.
func (h *AuthHandler) clearAuthCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{
		Name:     authCookieName,
		Value:    "",
		Path:     "/",
		MaxAge:   -1,
		HttpOnly: true,
		Secure:   h.cfg.IsProd(),
		SameSite: http.SameSiteStrictMode,
	})
}

// nonceResponse matches the Node.js response format.
type nonceResponse struct {
	Nonce   string `json:"nonce"`
	NonceID string `json:"nonceId"`
}

// HandleNonce handles POST /auth/nonce and GET /auth/nonce.
// Generates a new nonce and returns it with a nonceId.
func (h *AuthHandler) HandleNonce(w http.ResponseWriter, r *http.Request) {
	result, err := h.nonceService.Generate(r.Context())
	if err != nil {
		slog.Error("Failed to generate nonce", "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to generate nonce")
		return
	}

	h.auditLogger.LogRequest(r, db.AuditEvent{
		EventType: "nonce_issued",
		Severity:  "info",
	}, h.cfg.TrustProxyHeaders)

	writeJSON(w, http.StatusOK, nonceResponse{
		Nonce:   result.Nonce,
		NonceID: result.NonceID,
	})
}

// verifyRequest is the request body for POST /auth/verify.
type verifyRequest struct {
	WalletAddress string `json:"walletAddress"`
	Signature     string `json:"signature"`
	NonceID       string `json:"nonceId,omitempty"`
	Nonce         string `json:"nonce,omitempty"`
	// Chain selects the signature scheme: "solana" (default, ed25519 SIWS) or
	// "ethereum"/"evm" (EIP-191 SIWE). Enables multichain onboarding.
	Chain string `json:"chain,omitempty"`
}

// verifyResponse is the response from POST /auth/verify.
// The JWT is delivered via HttpOnly cookie only — not returned in the body —
// to prevent token exfiltration via XSS. Clients that previously stored the
// token from the body should use GET /auth/session to confirm auth state.
type verifyResponse struct {
	OK        bool   `json:"ok"`
	ExpiresAt string `json:"expiresAt"`
}

// HandleVerify handles POST /auth/verify.
// Validates the SIWS signature, issues a JWT, and logs the login.
func (h *AuthHandler) HandleVerify(w http.ResponseWriter, r *http.Request) {
	var req verifyRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "Invalid request body")
		return
	}

	// Validate required fields
	if req.WalletAddress == "" {
		writeError(w, http.StatusBadRequest, "walletAddress is required")
		return
	}
	if req.Signature == "" {
		writeError(w, http.StatusBadRequest, "signature is required")
		return
	}

	ip := extractIP(r, h.cfg.TrustProxyHeaders)

	// Resolve nonce via server-issued nonceId only.
	// Accepting a raw nonce value directly would bypass single-use enforcement and
	// allow replay attacks. All clients must obtain a nonceId from POST /auth/nonce.
	if req.NonceID == "" {
		slog.Warn("Login verification rejected: nonceId is required",
			"wallet", req.WalletAddress,
			"ip", ip,
		)
		h.logFailedLogin(r, req.WalletAddress, "", "nonceId is required")
		h.auditLogger.LogRequest(r, db.AuditEvent{
			EventType:     "login_failed",
			Severity:      "warning",
			WalletAddress: req.WalletAddress,
			EventData:     map[string]any{"reason": "nonceId is required"},
		}, h.cfg.TrustProxyHeaders)
		writeError(w, http.StatusBadRequest, "nonceId is required")
		return
	}
	consumed, err := h.nonceService.Consume(r.Context(), req.NonceID)
	if err != nil {
		slog.Error("Failed to consume nonce",
			"nonce_id", req.NonceID,
			"error", err,
		)
		writeError(w, http.StatusInternalServerError, "Failed to verify nonce")
		return
	}
	nonce := consumed

	if nonce == "" {
		slog.Warn("Login verification failed: nonce missing or expired",
			"wallet", req.WalletAddress,
			"ip", ip,
		)
		h.logFailedLogin(r, req.WalletAddress, "", "Nonce missing or expired")
		h.auditLogger.LogRequest(r, db.AuditEvent{
			EventType:     "login_failed",
			Severity:      "warning",
			WalletAddress: req.WalletAddress,
			EventData:     map[string]any{"reason": "nonce_missing_or_expired"},
		}, h.cfg.TrustProxyHeaders)
		writeError(w, http.StatusBadRequest, "Nonce missing or expired")
		return
	}

	// Construct the message that was signed: "OPRAI login: <nonce>"
	message := fmt.Sprintf("OPRAI login: %s", nonce)
	messageBytes := []byte(message)

	// Multichain: verify the signature with the scheme for the requested chain.
	// Solana (default) uses ed25519 SIWS; Ethereum uses EIP-191 SIWE (personal_sign).
	chainReq := strings.ToLower(strings.TrimSpace(req.Chain))
	isEVM := chainReq == "ethereum" || chainReq == "evm" || chainReq == "eip155"
	identityType, chainName := "solana_wallet", "solana"
	walletAddr := req.WalletAddress
	sigValid := false
	if isEVM {
		identityType, chainName = "evm_wallet", "ethereum"
		walletAddr = services.NormalizeEVMAddress(req.WalletAddress)
		if walletAddr == "" {
			writeError(w, http.StatusBadRequest, "Invalid EVM address")
			return
		}
		sigValid = services.VerifyEVMSignature(walletAddr, messageBytes, req.Signature)
	} else {
		sigValid = services.VerifySignature(req.WalletAddress, messageBytes, req.Signature)
	}
	if !sigValid {
		slog.Warn("Login verification failed: invalid signature", "wallet", walletAddr, "chain", chainName, "ip", ip)
		h.logFailedLogin(r, walletAddr, "", "Invalid signature")
		h.auditLogger.LogRequest(r, db.AuditEvent{
			EventType:     "invalid_signature",
			Severity:      "warning",
			WalletAddress: walletAddr,
			EventData:     map[string]any{"reason": "signature_mismatch", "chain": chainName},
		}, h.cfg.TrustProxyHeaders)
		writeError(w, http.StatusUnauthorized, "Invalid signature")
		return
	}

	// Resolve the ACCOUNT this wallet belongs to. A wallet may be a SECONDARY
	// identity linked to an existing account — log the user into THAT account,
	// don't spin up a new one. Only if the wallet is unknown do we create a fresh
	// account (with this wallet as its primary identity, on its own chain).
	accountID := ""
	if identity, ierr := h.queries.GetIdentityByTypeIdentifier(r.Context(), identityType, walletAddr); ierr == nil && identity != nil {
		accountID = identity.AccountID
	}
	if accountID == "" {
		user, err := h.queries.GetOrCreateUserChain(r.Context(), walletAddr, chainName)
		if err != nil {
			slog.Error("Failed to get or create user", "wallet", walletAddr, "chain", chainName, "error", err)
			writeError(w, http.StatusInternalServerError, "Failed to process user")
			return
		}
		accountID = user.ID
		if eerr := h.queries.EnsurePrimaryIdentityTyped(r.Context(), accountID, identityType, chainName, walletAddr); eerr != nil {
			slog.Warn("Failed to seed primary identity", "wallet", walletAddr, "error", eerr)
		}
	}

	// Log successful login (fire-and-forget, do not block response)
	go h.logSuccessLogin(r, walletAddr, accountID)
	h.auditLogger.LogRequest(r, db.AuditEvent{
		EventType:     "login_success",
		Severity:      "info",
		EntityType:    "user",
		EntityID:      accountID,
		UserID:        accountID,
		WalletAddress: walletAddr,
	}, h.cfg.TrustProxyHeaders)

	// Issue JWT — sub/wallet is the signing wallet (economics key), the `a` claim
	// carries the account (rewards/aggregation key).
	result, err := h.jwtService.Issue(walletAddr, accountID)
	if err != nil {
		slog.Error("Failed to issue JWT",
			"wallet", req.WalletAddress,
			"error", err,
		)
		writeError(w, http.StatusInternalServerError, "Failed to issue token")
		return
	}

	slog.Info("User login successful",
		"wallet", req.WalletAddress,
		"ip", ip,
	)

	// Deliver the JWT exclusively via HttpOnly cookie. The token is NOT returned
	// in the body to prevent extraction via XSS. Clients must use GET /auth/session
	// to confirm authentication state.
	h.setAuthCookie(w, result.Token)

	writeJSON(w, http.StatusOK, verifyResponse{
		OK:        true,
		ExpiresAt: result.ExpiresAt,
	})
}

// sessionResponse matches the Node.js response format.
type sessionResponse struct {
	Authenticated bool   `json:"authenticated"`
	Wallet        string `json:"wallet,omitempty"`
	ExpiresAt     string `json:"expiresAt,omitempty"`
}

// HandleSession handles GET /auth/session.
// Checks the current JWT from Authorization header or HttpOnly cookie.
func (h *AuthHandler) HandleSession(w http.ResponseWriter, r *http.Request) {
	// Resolve token: Bearer header takes priority, then cookie fallback.
	var tokenStr string
	if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
		tokenStr = authHeader[7:]
	} else if cookie, err := r.Cookie(authCookieName); err == nil {
		tokenStr = cookie.Value
	}

	if tokenStr == "" {
		writeJSON(w, http.StatusOK, sessionResponse{Authenticated: false})
		return
	}

	wallet, expiresAt, err := h.jwtService.Validate(tokenStr)
	if err != nil {
		writeJSON(w, http.StatusOK, sessionResponse{Authenticated: false})
		return
	}

	// Reject a logged-out (revoked) token even though the gateway blocklist
	// already does — defence in depth on the now-shared revocation keyspace, and
	// correct behaviour for any future direct caller of the auth-service.
	if jti, _, perr := h.jwtService.ParseUnvalidated(tokenStr); perr == nil &&
		h.revocationService.IsRevoked(r.Context(), jti) {
		writeJSON(w, http.StatusOK, sessionResponse{Authenticated: false})
		return
	}

	writeJSON(w, http.StatusOK, sessionResponse{
		Authenticated: true,
		Wallet:        wallet,
		ExpiresAt:     expiresAt.UTC().Format("2006-01-02T15:04:05.000Z"),
	})
}

// HandleLogout handles POST /auth/logout.
// Revokes the presented JWT (by jti), clears the auth cookie, and confirms logout.
func (h *AuthHandler) HandleLogout(w http.ResponseWriter, r *http.Request) {
	wallet := middleware.WalletFromContext(r.Context())

	// Extract the token from header or cookie and revoke its jti so that any
	// copy of the token (e.g. stored in localStorage by an older client build)
	// is also invalidated before it naturally expires.
	var tokenStr string
	if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
		tokenStr = strings.TrimPrefix(authHeader, "Bearer ")
	} else if cookie, err := r.Cookie(authCookieName); err == nil {
		tokenStr = cookie.Value
	}
	if tokenStr != "" {
		jti, expiry, err := h.jwtService.ParseUnvalidated(tokenStr)
		if err == nil && jti != "" {
			h.revocationService.Revoke(r.Context(), jti, expiry)
		}
	}

	h.clearAuthCookie(w)
	if wallet != "" {
		h.auditLogger.LogRequest(r, db.AuditEvent{
			EventType:     "logout",
			Severity:      "info",
			WalletAddress: wallet,
		}, h.cfg.TrustProxyHeaders)
	}
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
}

// logSuccessLogin records a successful login in the login_logs table.
// Called as a goroutine — uses a detached context so it is not cancelled when
// the parent HTTP handler returns.
func (h *AuthHandler) logSuccessLogin(r *http.Request, walletAddress, userID string) {
	ipAddress := extractIP(r, h.cfg.TrustProxyHeaders)
	userAgent := r.Header.Get("User-Agent")
	if userAgent == "" {
		userAgent = "unknown"
	}

	loginLog := &db.LoginLog{
		UserID:        userID,
		WalletAddress: walletAddress,
		IPAddress:     ipAddress,
		UserAgent:     userAgent,
		Success:       true,
	}

	// Use a background context with a short timeout — r.Context() is cancelled
	// as soon as the HTTP response is sent, which would silently drop this write.
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := h.queries.InsertLoginLog(ctx, loginLog); err != nil {
		slog.Error("Failed to persist login log entry",
			"wallet", walletAddress,
			"error", err,
		)
	}
}

// logFailedLogin records a failed login attempt.
func (h *AuthHandler) logFailedLogin(r *http.Request, walletAddress, userID, reason string) {
	ipAddress := extractIP(r, h.cfg.TrustProxyHeaders)
	userAgent := r.Header.Get("User-Agent")
	if userAgent == "" {
		userAgent = "unknown"
	}

	loginLog := &db.LoginLog{
		UserID:        userID,
		WalletAddress: walletAddress,
		IPAddress:     ipAddress,
		UserAgent:     userAgent,
		Success:       false,
	}
	// Set failure reason
	loginLog.FailureReason.String = reason
	loginLog.FailureReason.Valid = reason != ""

	if err := h.queries.InsertLoginLog(r.Context(), loginLog); err != nil {
		slog.Error("Failed to persist failed login log entry",
			"wallet", walletAddress,
			"reason", reason,
			"error", err,
		)
	}
}

// extractIP returns the client IP address from the request.
//
// When trustProxy is false (the default), r.RemoteAddr is always used,
// making it impossible to spoof the client IP via headers.
//
// When trustProxy is true — only set this when the service runs behind a
// known trusted reverse proxy — the leftmost value of X-Forwarded-For is
// used, falling back to X-Real-IP and then RemoteAddr.
func extractIP(r *http.Request, trustProxy bool) string {
	if trustProxy {
		if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
			parts := strings.SplitN(xff, ",", 2)
			if ip := strings.TrimSpace(parts[0]); ip != "" {
				return ip
			}
		}
		if xri := r.Header.Get("X-Real-IP"); xri != "" {
			return strings.TrimSpace(xri)
		}
	}

	// Fall back to the actual TCP connection address. Strip the port.
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

// writeJSON encodes v as JSON and writes it with the given status code.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Error("Failed to encode JSON response", "error", err)
	}
}

// writeError writes a JSON error response.
func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}

// WalletFromContext re-exports the middleware helper for use in handlers.
func WalletFromContext(r *http.Request) string {
	return middleware.WalletFromContext(r.Context())
}
