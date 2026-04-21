package server

import (
	"crypto/subtle"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/go-chi/cors"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/oprai/oprai/services/admin-service-go/internal/config"
	"github.com/oprai/oprai/services/admin-service-go/internal/db"
	"github.com/oprai/oprai/services/admin-service-go/internal/handlers"
	"github.com/oprai/oprai/services/admin-service-go/internal/middleware"
	"github.com/oprai/oprai/services/admin-service-go/internal/services"
)

// NewHTTPServer creates and configures the Chi router with all admin routes.
func NewHTTPServer(cfg *config.Config, queries *db.Queries) http.Handler {
	r := chi.NewRouter()

	// Global middleware
	r.Use(chimiddleware.RequestID)
	// RealIP is only enabled when TRUST_PROXY_HEADERS=true — identical to the
	// gateway pattern. Without it, r.RemoteAddr is always the raw TCP peer address,
	// preventing X-Forwarded-For spoofing of IP audit logs and brute-force counters.
	if cfg.TrustProxyHeaders {
		r.Use(chimiddleware.RealIP)
	}
	r.Use(chimiddleware.Recoverer)
	r.Use(chimiddleware.Timeout(30 * time.Second))
	r.Use(middleware.SecurityHeaders)
	r.Use(middleware.MetricsMiddleware)

	// CORS locked to admin panel origin
	r.Use(cors.Handler(cors.Options{
		AllowedOrigins:   []string{cfg.AdminCORSOrigin},
		AllowedMethods:   []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"Accept", "Authorization", "Content-Type", "X-Requested-With"},
		AllowCredentials: true,
		MaxAge:           300,
	}))

	// Services
	authService := services.NewAdminAuth(cfg)

	isProd := cfg.Environment == "production"
	// Handlers
	authHandler := handlers.NewAuthHandler(authService, queries, isProd, cfg.AdminJWTTTL)
	dashboardHandler := handlers.NewDashboardHandler(queries)
	userHandler := handlers.NewUserHandler(queries)
	transactionHandler := handlers.NewTransactionHandler(queries)
	sessionHandler := handlers.NewSessionHandler(queries)
	ipLogHandler := handlers.NewIPLogHandler(queries)
	auditHandler := handlers.NewAuditHandler(queries)
	exportHandler := handlers.NewExportHandler(queries)
	analyticsHandler := handlers.NewAnalyticsHandler(queries)
	adminSessionHandler := handlers.NewAdminSessionHandler(queries)

	// Admin auth middleware
	adminAuth := middleware.NewAdminAuth(authService)

	// Health check (public) — verifies DB connectivity so load balancers get an
	// accurate signal: if DB is down, service reports unhealthy.
	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		if err := queries.Ping(r.Context()); err != nil {
			writeJSON(w, http.StatusServiceUnavailable, map[string]interface{}{
				"status":    "unhealthy",
				"service":   "admin-service",
				"error":     "database unreachable",
				"timestamp": time.Now().UTC().Format(time.RFC3339),
			})
			return
		}
		writeJSON(w, http.StatusOK, map[string]interface{}{
			"status":    "ok",
			"service":   "admin-service",
			"timestamp": time.Now().UTC().Format(time.RFC3339),
		})
	})

	// Metrics endpoint — restricted to internal scrapers via X-Internal-Api-Key.
	r.Handle("/metrics", internalKeyGate(cfg.InternalAPIKey, promhttp.Handler()))

	// Auth routes (public)
	r.Route("/admin/auth", func(r chi.Router) {
		r.Post("/login", authHandler.Login)
		r.Post("/logout", authHandler.Logout)
		r.With(adminAuth.Middleware).Get("/verify", authHandler.Verify)
	})

	// Protected admin routes
	r.Group(func(r chi.Router) {
		r.Use(adminAuth.Middleware)

		// Dashboard
		r.Get("/admin/dashboard/stats", dashboardHandler.GetStats)

		// Users
		r.Route("/admin/users", func(r chi.Router) {
			r.Get("/", userHandler.ListUsers)
			r.Get("/{id}", userHandler.GetUser)
			r.Get("/{id}/overview", userHandler.GetUserOverview)
			r.Get("/{id}/sessions", userHandler.GetUserSessions)
			r.Get("/{id}/transactions", userHandler.GetUserTransactions)
			r.Get("/{id}/ip-logs", userHandler.GetUserIPLogs)
			r.Get("/{id}/audit-logs", userHandler.GetUserAuditLogs)
			r.With(middleware.RequireAdmin).Patch("/{id}/status", middleware.AuditAction(queries, "user.status_change")(userHandler.UpdateUserStatus))
			r.With(middleware.RequireAdmin).Patch("/{id}/role", middleware.AuditAction(queries, "user.role_change")(userHandler.UpdateUserRole))
		})

		// Transactions
		r.Route("/admin/transactions", func(r chi.Router) {
			r.Get("/", transactionHandler.ListTransactions)
			r.Get("/{id}", transactionHandler.GetTransaction)
		})

		// Sessions
		r.Route("/admin/sessions", func(r chi.Router) {
			r.Get("/", sessionHandler.ListSessions)
			r.Get("/{id}/messages", sessionHandler.GetSessionMessages)
			r.With(middleware.RequireAdmin).Post("/{id}/close", middleware.AuditAction(queries, "session.close")(sessionHandler.CloseSession))
		})

		// IP logs (enhanced)
		r.Route("/admin/ip-logs", func(r chi.Router) {
			r.Get("/", ipLogHandler.ListIPLogs)
			r.Get("/suspicious", ipLogHandler.ListSuspiciousIPs)
			r.Get("/by-ip/{ip}", ipLogHandler.GetByIP)
			r.Post("/refresh", middleware.AuditAction(queries, "ip_summary.refresh")(ipLogHandler.RefreshIPSummary))
		})

		// Audit logs
		r.Get("/admin/audit-logs", auditHandler.ListAuditLogs)

		// Admin users management — mutations require superadmin role.
		r.Route("/admin/admin-users", func(r chi.Router) {
			r.Get("/", authHandler.ListAdmins) // any authenticated admin may list
			r.With(middleware.RequireSuperAdmin).Post("/", middleware.AuditAction(queries, "admin.create")(authHandler.CreateAdmin))
			r.With(middleware.RequireSuperAdmin).Delete("/{id}", middleware.AuditAction(queries, "admin.delete")(authHandler.DeleteAdmin))
			r.With(middleware.RequireSuperAdmin).Patch("/{id}/password", middleware.AuditAction(queries, "admin.password_reset")(authHandler.ResetPassword))
		})

		// Admin sessions
		r.Get("/admin/admin-sessions", adminSessionHandler.ListAdminSessions)

		// Analytics
		r.Route("/admin/analytics", func(r chi.Router) {
			r.Get("/timeseries", analyticsHandler.GetTimeseries)
			r.Get("/top-wallets", analyticsHandler.GetTopWallets)
			r.Get("/tx-by-protocol", analyticsHandler.GetTxByProtocol)
		})

		// Export — bulk data export requires superadmin role (PII/sensitive data).
		r.Route("/admin/export", func(r chi.Router) {
			r.Use(middleware.RequireSuperAdmin)
			r.Get("/users", middleware.AuditAction(queries, "data.export")(exportHandler.ExportUsers))
			r.Get("/transactions", middleware.AuditAction(queries, "data.export")(exportHandler.ExportTransactions))
			r.Get("/sessions", middleware.AuditAction(queries, "data.export")(exportHandler.ExportSessions))
			r.Get("/audit-logs", middleware.AuditAction(queries, "data.export")(exportHandler.ExportAuditLogs))
		})

		// Aliases matching frontend API calls that differ from canonical backend paths.
		r.With(middleware.RequireSuperAdmin).Get("/admin/audit-logs/export",
			middleware.AuditAction(queries, "data.export")(exportHandler.ExportAuditLogs))
		r.With(middleware.RequireSuperAdmin).Post("/admin/admin-users/{id}/reset-password",
			middleware.AuditAction(queries, "admin.password_reset")(authHandler.ResetPassword))
	})

	return r
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
