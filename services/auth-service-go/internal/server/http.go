package server

import (
	"crypto/subtle"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/go-chi/cors"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/oprai/oprai/services/auth-service-go/internal/audit"
	"github.com/oprai/oprai/services/auth-service-go/internal/config"
	"github.com/oprai/oprai/services/auth-service-go/internal/db"
	"github.com/oprai/oprai/services/auth-service-go/internal/handlers"
	"github.com/oprai/oprai/services/auth-service-go/internal/middleware"
	"github.com/oprai/oprai/services/auth-service-go/internal/services"
)

// NewHTTPServer creates and configures the Chi HTTP router with all routes.
func NewHTTPServer(
	cfg *config.Config,
	jwtService *services.JWTService,
	nonceService *services.NonceService,
	revocationService *services.RevocationService,
	queries *db.Queries,
	auditLogger *audit.Logger,
) *http.Server {
	r := chi.NewRouter()

	// ── Global middleware ──────────────────────────────────────────────
	r.Use(chimiddleware.RequestID)
	// Only trust X-Forwarded-For / X-Real-IP when explicitly configured.
	// When false, r.RemoteAddr is always the raw TCP peer address, making
	// IP spoofing via headers impossible.
	if cfg.TrustProxyHeaders {
		r.Use(chimiddleware.RealIP)
	}
	r.Use(chimiddleware.Logger)
	r.Use(chimiddleware.Recoverer)
	r.Use(middleware.SecurityHeaders)
	r.Use(middleware.Metrics)
	r.Use(middleware.RequestAudit(auditLogger, cfg.TrustProxyHeaders))

	// CORS — must use a specific origin (not "*") when AllowCredentials is true
	// so that browsers accept Set-Cookie response headers from this service.
	r.Use(cors.Handler(cors.Options{
		AllowedOrigins:   []string{cfg.CORSOrigin},
		AllowedMethods:   []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"Accept", "Authorization", "Content-Type", "X-User-Wallet", "X-Internal-Api-Key"},
		ExposedHeaders:   []string{"Link"},
		AllowCredentials: true,
		MaxAge:           300,
	}))

	// Request timeout
	r.Use(chimiddleware.Timeout(30 * time.Second))

	// ── Auth middleware instance ───────────────────────────────────────
	auth := middleware.NewAuth(jwtService, cfg.InternalAPIKey)

	// ── Per-IP rate limiter for authentication endpoints ──────────────
	// 10 attempts per 15-minute window per IP. Defends against credential
	// stuffing and nonce-flooding attacks. The gateway already limits to
	// 20/min globally; this adds defence-in-depth at the service level.
	authRateLimiter := middleware.NewIPRateLimiter(10, 15*time.Minute)

	// ── Handlers ──────────────────────────────────────────────────────
	authHandler := handlers.NewAuthHandler(nonceService, jwtService, revocationService, queries, cfg, auditLogger)
	userHandler := handlers.NewUserHandler(queries, auditLogger, cfg)
	accountHandler := handlers.NewAccountHandler(queries, nonceService)
	spendingHandler := handlers.NewSpendingHandler(queries)

	// ── Health check ──────────────────────────────────────────────────
	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		fmt.Fprintf(w, `{"status":"ok","service":"auth-service-go","timestamp":"%s"}`,
			time.Now().UTC().Format(time.RFC3339))
	})

	// ── Metrics endpoint ──────────────────────────────────────────────
	// Restricted to internal scrapers; prevents enumeration of request
	// patterns and performance data by external callers.
	r.Handle("/metrics", internalKeyGate(cfg.InternalAPIKey, promhttp.Handler()))

	// ── Auth routes (public) ──────────────────────────────────────────
	authRL := middleware.RateLimit(authRateLimiter, cfg.TrustProxyHeaders)
	r.Route("/auth", func(r chi.Router) {
		r.With(authRL).Post("/nonce", authHandler.HandleNonce)
		r.With(authRL).Get("/nonce", authHandler.HandleNonce) // backward compat
		r.With(authRL).Post("/verify", authHandler.HandleVerify)
		r.Get("/session", authHandler.HandleSession)
		r.Post("/logout", authHandler.HandleLogout)
		r.Delete("/session", func(w http.ResponseWriter, r *http.Request) {
			// JWT is stateless; client should discard the token
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			w.Write([]byte(`{"ok":true}`))
		})
	})

	// ── User routes (protected) ───────────────────────────────────────
	r.Route("/users", func(r chi.Router) {
		r.Use(auth.Required)

		r.Get("/me", userHandler.HandleGetMe)
		r.Put("/me", userHandler.HandleUpdateMe)

		// Spending limits — server-side enforcement complement to client-side UX
		r.Get("/me/spending-limits", spendingHandler.HandleGetSpendingLimits)
		r.Put("/me/spending-limits", spendingHandler.HandleUpsertSpendingLimits)

		r.Post("/", userHandler.HandleCreateUser)
		r.Get("/{wallet}", userHandler.HandleGetByWallet)
		r.Patch("/{wallet}", userHandler.HandlePatchByWallet)
	})

	// Account view + identity linking — the multichain identity surface.
	r.Route("/account", func(r chi.Router) {
		r.Use(auth.Required)
		r.Get("/me", accountHandler.HandleGetMe)
		r.Post("/link/nonce", accountHandler.HandleLinkNonce)
		r.Post("/link/verify", accountHandler.HandleLinkVerify)
	})

	// ── Internal routes (gated by X-Internal-Api-Key) ─────────────────
	// solana-service-rs calls /internal/spending/check before building
	// any fund-moving transaction (hard-stop) and /internal/spending/commit
	// after /actions/submit succeeds. Frontend never hits these.
	r.Route("/internal/spending", func(r chi.Router) {
		r.Use(func(next http.Handler) http.Handler {
			return internalKeyGate(cfg.InternalAPIKey, next)
		})
		r.Post("/check", spendingHandler.HandleCheckSpending)
		r.Post("/commit", spendingHandler.HandleCommitSpending)
	})

	// Wallet -> real users.id resolution for solana-service (so it never reads
	// auth_schema directly). Internal-only.
	r.Route("/internal/users", func(r chi.Router) {
		r.Use(func(next http.Handler) http.Handler {
			return internalKeyGate(cfg.InternalAPIKey, next)
		})
		r.Get("/by-wallet/{wallet}", spendingHandler.HandleResolveUserID)
	})

	server := &http.Server{
		Addr:         fmt.Sprintf(":%d", cfg.Port),
		Handler:      r,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	slog.Info("HTTP server configured", "port", cfg.Port)
	return server
}

// internalKeyGate wraps h so that only requests carrying the correct
// X-Internal-Api-Key header are forwarded. Used to restrict /metrics to
// internal Prometheus scrapers. When key is empty the check is skipped so
// local development without a configured key still works.
func internalKeyGate(key string, h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if key != "" {
			provided := r.Header.Get("X-Internal-Api-Key")
			if subtle.ConstantTimeCompare([]byte(provided), []byte(key)) != 1 {
				http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
				return
			}
		}
		h.ServeHTTP(w, r)
	})
}
