package server

import (
	"context"
	"crypto/subtle"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/oprai/oprai/services/gateway-go/internal/config"
	"github.com/oprai/oprai/services/gateway-go/internal/handlers"
	"github.com/oprai/oprai/services/gateway-go/internal/middleware"
	"github.com/oprai/oprai/services/gateway-go/internal/proxy"
)

// NewRouter creates and configures the Chi router with all gateway routes.
// ctx is the application root context; background goroutines started by the
// router (rate-limiter cleanup, cache cleanup) will stop when ctx is cancelled.
func NewRouter(ctx context.Context, cfg *config.Config, grpcClients *proxy.GRPCClients) http.Handler {
	r := chi.NewRouter()

	// Global middleware
	r.Use(chimiddleware.RequestID)
	// NOTE: chimiddleware.RealIP is intentionally NOT used here. IP extraction
	// is handled in the rate limiter using the TrustProxyHeaders config flag,
	// preventing X-Forwarded-For spoofing when not behind a trusted proxy.
	r.Use(chimiddleware.Recoverer)
	// NOTE: Global 30s timeout NOT applied here — SSE streaming routes need longer.
	// Non-streaming routes get 30s timeout via defaultTimeout middleware below.

	// Per-IP rate-limiter stores — background cleanup goroutines stop when ctx is done.
	globalRLStore := middleware.NewGlobalRateLimiterStore(ctx)
	authRLStore := middleware.NewAuthRateLimiterStore(ctx)
	walletRLStore := middleware.NewWalletRateLimiterStore(ctx)

	// JWT revocation blocklist — revoked jtis are added here on logout so that
	// copied Bearer tokens are rejected immediately without waiting for expiry.
	tokenBlocklist := middleware.NewTokenBlocklist(ctx)

	// Custom middleware
	r.Use(middleware.SecurityHeaders)
	r.Use(middleware.CORSMiddleware(cfg.CORSOrigin))
	r.Use(middleware.MetricsMiddleware)
	r.Use(middleware.LoggerMiddleware)
	r.Use(middleware.GlobalRateLimit(globalRLStore, cfg.TrustProxyHeaders)) // 100/min global

	// JWT validation middleware:
	//   no token → pass through (public endpoints)
	//   invalid/expired token → 401 Unauthorized (RFC 6750)
	//   revoked jti → 401 Unauthorized (logged-out token)
	//   valid token → wallet set in context + X-User-Wallet header injected
	r.Use(middleware.JWTAuth(cfg.JWTSecret, tokenBlocklist))

	// Per-wallet rate limit applied after JWTAuth (so wallet is in context).
	// 60 req/min per authenticated wallet — prevents a single wallet from
	// monopolising backend resources even from different IPs.
	r.Use(middleware.WalletRateLimit(walletRLStore))

	// CSRF defence: X-Requested-With check + Origin/Referer whitelist validation.
	// Both Angular frontends set X-Requested-With via their HTTP interceptors.
	r.Use(middleware.CSRFProtection(cfg.CORSOrigin))

	// Metrics endpoint — restricted to internal scrapers via X-Internal-Api-Key.
	// Prevents external callers from enumerating request patterns and performance data.
	r.Handle("/metrics", internalKeyGate(cfg.InternalAPIKey, promhttp.Handler()))

	// Health check - aggregated from all backend services
	healthHandler := handlers.NewHealthHandler(grpcClients)
	r.Get("/health", healthHandler.AggregatedHealth)

	// Default 30s timeout for non-streaming routes
	defaultTimeout := chimiddleware.Timeout(30 * time.Second)
	// Context-only deadline for SSE streaming routes.
	// chimiddleware.Timeout wraps the ResponseWriter (buffers writes), which
	// completely breaks Server-Sent Events. This middleware only sets a context
	// deadline so the handler can detect timeouts without blocking flushes.
	streamingTimeout := func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ctx, cancel := context.WithTimeout(r.Context(), 5*time.Minute)
			defer cancel()
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}

	// Auth proxy — HTTP reverse proxy (with stricter rate limiting)
	authProxy := handlers.NewAuthProxy(cfg.AuthServiceHTTP, cfg.InternalAPIKey, tokenBlocklist, cfg.JWTSecret)
	r.Route("/auth", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.AuthRateLimit(authRLStore, cfg.TrustProxyHeaders)) // 20/min for auth
		r.Post("/nonce", authProxy.PostNonce)
		r.Post("/verify", authProxy.PostVerify)
		r.Get("/session", authProxy.GetSession)   // used by frontend to restore session from cookie
		r.Post("/logout", authProxy.PostLogout)   // clears HttpOnly cookie
		r.Get("/me", authProxy.GetMe)
	})
	r.Route("/users", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Get("/me", authProxy.GetMe)
		r.Get("/me/spending-limits", authProxy.GetSpendingLimits)
		r.Put("/me/spending-limits", authProxy.PutSpendingLimits)
		r.Get("/{wallet}", authProxy.GetUser)
	})

	// Chat proxy — HTTP reverse proxy, routes match Angular frontend URL structure
	chatProxy := handlers.NewChatProxy(cfg.ChatServiceHTTP, cfg.InternalAPIKey)
	r.Route("/chat", func(r chi.Router) {
		r.With(defaultTimeout).Post("/", chatProxy.SendChat)
		r.With(streamingTimeout).Get("/stream", chatProxy.StreamChat)
		r.With(streamingTimeout).Post("/messages/stream", chatProxy.StreamMessagesPost)

		r.Route("/sessions", func(r chi.Router) {
			r.Use(defaultTimeout)
			r.Get("/", chatProxy.ListSessions)
			r.Post("/", chatProxy.CreateSession)
			r.Get("/{id}", chatProxy.GetSession)
			r.Delete("/{id}", chatProxy.DeleteSession)
			r.Patch("/{id}", chatProxy.UpdateSession)
			r.Patch("/{id}/pin", chatProxy.PinSession)
			r.Get("/{id}/messages", chatProxy.GetMessages)
			r.Post("/{id}/messages", chatProxy.SendMessage)
			r.With(streamingTimeout).Get("/{id}/messages/stream", chatProxy.StreamMessages)
		})
	})

	// Tax report — proxied to chat-service (requires wallet auth)
	r.Route("/tax", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet)
		r.Post("/report", chatProxy.TaxReport)
		r.Post("/export", chatProxy.TaxExport)
		r.Get("/events/{year}", chatProxy.TaxEvents)
		r.Get("/years", chatProxy.TaxYears)
	})

	// Keep legacy /sessions routes for backward compatibility
	r.Route("/sessions", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Get("/", chatProxy.ListSessions)
		r.Post("/", chatProxy.CreateSession)
		r.Get("/{id}", chatProxy.GetSession)
		r.Delete("/{id}", chatProxy.DeleteSession)
		r.Get("/{id}/messages", chatProxy.GetMessages)
		r.Post("/{id}/messages", chatProxy.SendMessage)
		r.With(streamingTimeout).Get("/{id}/messages/stream", chatProxy.StreamMessages)
	})

	// Solana proxy — wallet auth required for all transaction-building routes
	solanaProxy := handlers.NewSolanaProxy(cfg.SolanaServiceHTTP, cfg.InternalAPIKey)
	r.Route("/actions", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet)
		r.Post("/quote", solanaProxy.PostQuote)
		r.Post("/build", solanaProxy.PostBuild)
		r.Post("/submit", solanaProxy.PostSubmit)
		r.Get("/limit-orders", solanaProxy.GetLimitOrders)
		r.Get("/dca-orders", solanaProxy.GetDcaOrders)
	})
	r.Route("/protocols", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Get("/", solanaProxy.ListProtocols)
		r.Get("/{protocol}", solanaProxy.GetProtocol)
	})
	r.Route("/tokens", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Get("/", solanaProxy.ListTokens)
		r.Get("/{symbol}", solanaProxy.GetToken)
	})
	r.Route("/transactions", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet) // wallet-scoped data — must be authenticated
		r.Get("/", solanaProxy.ListTransactions)
		r.Get("/{id}", solanaProxy.GetTransaction)
	})
	r.Route("/balance", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet) // wallet-scoped data — must be authenticated
		r.Get("/", solanaProxy.GetBalance)
	})

	// Memory proxy — HTTP reverse proxy
	memoryProxy := handlers.NewMemoryProxy(cfg.MemoryServiceHTTP, cfg.InternalAPIKey)
	r.Route("/memory", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Get("/", memoryProxy.GetMemories)
		r.Post("/", memoryProxy.StoreMemory)
		r.Delete("/", memoryProxy.ClearMemories)
		r.Get("/search", memoryProxy.SearchMemories)
		r.Delete("/{id}", memoryProxy.DeleteMemoryById)
	})
	r.Route("/consent", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Get("/", memoryProxy.GetConsent)
		r.Put("/", memoryProxy.UpdateConsent)
	})
	r.With(defaultTimeout).Post("/summarize", memoryProxy.Summarize)

	// Market data proxy — external APIs (Jupiter, DexScreener, Birdeye).
	// RequireWallet prevents unauthenticated callers from exhausting paid API quotas
	// (Birdeye, Helius, Jupiter keys are kept server-side).
	marketProxy := handlers.NewMarketProxy(ctx, cfg.BirdeyeAPIKey, cfg.JupiterAPIKey, cfg.HeliusAPIKey)
	r.Route("/market", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet)
		r.Get("/prices", marketProxy.GetPrices)
		r.Get("/tokens/search", marketProxy.SearchTokens)
		r.Get("/tokens/strict", marketProxy.GetTokenList)
		r.Get("/tokens/{mint}", marketProxy.GetTokenInfo)
		r.Get("/account/{wallet}/transactions", marketProxy.GetAccountTransactions)
		r.Get("/ohlcv", marketProxy.GetOHLCV)
		r.Get("/trending", marketProxy.GetTrending)
		r.Get("/analytics/{mint}", marketProxy.GetAnalytics)
		r.Get("/pairs/{mint}", marketProxy.GetPairs)
		r.Get("/trades/{mint}", marketProxy.GetTrades)
		r.Get("/holders/{mint}", marketProxy.GetHolders)
		r.Get("/jito/tip-floor", marketProxy.GetJitoTipFloor)
		r.Get("/jito/bundle/{bundleId}", marketProxy.GetJitoBundleStatus)
		// Kobe analytics API (kobe.mainnet.jito.network) — proxied to avoid CORS
		r.Post("/jito/stake-pool-stats", marketProxy.GetJitoStakePoolStats)
		r.Post("/jito/jitosol-sol-ratio", marketProxy.GetJitosolSolRatio)
		r.Post("/jito/staker-rewards", marketProxy.PostJitoStakerRewards)
		r.Post("/jito/validator-rewards", marketProxy.PostJitoValidatorRewards)
		r.Post("/jito/validators", marketProxy.PostJitoValidators)
		r.Post("/jito/jitosol-validators", marketProxy.PostJitosolValidators)
		r.Get("/jito/validators/{voteAccount}", marketProxy.GetJitoValidatorHistory)
		r.Get("/jito/mev-rewards", marketProxy.GetJitoMevRewards)
		r.Post("/jito/mev-rewards", marketProxy.GetJitoMevRewards)
		r.Get("/jito/daily-mev-rewards", marketProxy.GetJitoDailyMevRewards)
		r.Get("/jito/stake-over-time", marketProxy.GetJitoStakeOverTime)
		r.Get("/jito/preferred-withdraw-validators", marketProxy.GetJitoPreferredWithdrawValidators)
		r.Get("/jito/mev-commission-avg", marketProxy.GetJitoMevCommissionAverageOverTime)
		r.Post("/jito/steward-events", marketProxy.PostJitoStewardEvents)
		r.Get("/jito/bam-epoch-metrics", marketProxy.GetJitoBamEpochMetrics)
		r.Get("/jito/bam-validators", marketProxy.GetJitoBamValidators)
		r.Get("/jito/bam-delegation-blacklist", marketProxy.GetJitoBamDelegationBlacklist)
		r.Get("/jito/bam-validator-score", marketProxy.GetJitoBamValidatorScore)
		r.Get("/security/{mint}", marketProxy.GetSecurity)
		r.Get("/latest-pairs", marketProxy.GetLatestPairs)
		r.Get("/txns/{mint}", marketProxy.GetMintTransactions)
		r.Post("/helius/transactions", marketProxy.PostHeliusTransactions)
	})

	// Solana JSON-RPC proxy — keeps Helius API key server-side.
	// RequireWallet prevents unauthenticated quota abuse.
	r.With(defaultTimeout, middleware.RequireWallet).Post("/rpc", marketProxy.PostRpc)

	// Upload handler — stores files locally, serves via /uploads/*
	uploadHandler := handlers.NewUploadHandler(cfg.UploadDir, cfg.PublicBaseURL)
	r.Route("/upload", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet)
		r.Post("/image", uploadHandler.UploadImage)
		r.Post("/metadata", uploadHandler.UploadMetadata)
	})
	// Serve uploaded files as static assets (public, no auth required)
	staticHandler := handlers.NewStaticHandler(cfg.UploadDir, cfg.PublicBaseURL)
	r.Get("/uploads/*", staticHandler.ServeFile)

	// Pump.fun proxy — proxies pump.fun data APIs through our backend.
	// Token data (GET) is public; transaction-building (POST) requires wallet auth.
	pumpfunProxy := handlers.NewPumpFunProxy()
	r.Route("/pumpfun", func(r chi.Router) {
		r.Use(defaultTimeout)
		// ── Data endpoints (public, no wallet required) ──────────────────────
		r.Get("/tokens", pumpfunProxy.ListTokens)
		r.Get("/tokens/trending", pumpfunProxy.GetTrending)
		r.Get("/tokens/new", pumpfunProxy.GetNew)
		r.Get("/tokens/graduating", pumpfunProxy.GetGraduating)
		r.Get("/tokens/search", pumpfunProxy.SearchTokens)
		r.Get("/token/{mint}", pumpfunProxy.GetToken)
		r.Get("/token/{mint}/comments", pumpfunProxy.GetTokenComments)
		r.Get("/koth", pumpfunProxy.GetKingOfHill)
		r.Get("/user/{wallet}", pumpfunProxy.GetUserProfile)
		// ── Transaction endpoints (wallet auth required) ──────────────────────
		r.With(middleware.RequireWallet).Post("/buy", solanaProxy.PostBuild)
		r.With(middleware.RequireWallet).Post("/sell", solanaProxy.PostBuild)
		r.With(middleware.RequireWallet).Post("/launch", solanaProxy.PostBuild)
	})

	// DeFi liquidation proxy — wallet auth required so only authenticated users
	// can query health ratios (prevents scraping via our server-side proxy).
	liquidationProxy := handlers.NewLiquidationProxy()
	r.With(defaultTimeout, middleware.RequireWallet).Get("/defi/liquidations", liquidationProxy.GetPositions)

	return r
}

// internalKeyGate wraps h so that only requests carrying the correct
// X-Internal-Api-Key header are forwarded. Used to restrict /metrics to
// internal Prometheus scrapers. When key is empty (dev with no key set)
// the check is skipped so local development still works without configuration.
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
