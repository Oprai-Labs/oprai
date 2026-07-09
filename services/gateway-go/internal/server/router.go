package server

import (
	"context"
	"crypto/subtle"
	"net/http"
	"os"
	"time"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"

	"github.com/oprai/oprai/services/gateway-go/internal/config"
	"github.com/oprai/oprai/services/gateway-go/internal/handlers"
	"github.com/oprai/oprai/services/gateway-go/internal/middleware"
	"github.com/oprai/oprai/services/gateway-go/internal/proxy"
)

// NewRouter creates and configures the Chi router with all gateway routes.
// ctx is the application root context; background goroutines started by the
// router (rate-limiter cleanup, cache cleanup) will stop when ctx is cancelled.
// rdb is optional — when non-nil, the JWT revocation blocklist persists to
// Redis (so revoked tokens survive a gateway restart and are visible to all
// instances).
func NewRouter(ctx context.Context, cfg *config.Config, grpcClients *proxy.GRPCClients, rdb *redis.Client) http.Handler {
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
	// Backed by Redis (when configured) so that revocations survive a gateway
	// restart and are visible across multiple gateway instances.
	tokenBlocklist := middleware.NewTokenBlocklist(ctx, rdb)

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
	r.Use(middleware.JWTAuth(cfg.JWTSecret, cfg.JWTPreviousSecret, tokenBlocklist))

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
		r.With(streamingTimeout).Post("/messages/edit", chatProxy.EditMessage)

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
			r.Patch("/{id}/messages/{msgId}/metadata", chatProxy.PatchMessageMeta)
		})

		// Per-message thumbs feedback. The chat-service exposes this at
		// the top level (no session_id needed) — it derives the session
		// from message ownership and the wallet from auth.
		r.With(defaultTimeout).Post("/messages/{msgId}/feedback", chatProxy.PostMessageFeedback)
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

	// Portfolio analytics — per-token cost basis & all-time PnL.
	// Proxied to chat-service which persists into chat_schema and parses
	// Helius enhanced transactions. Wallet auth required; chat-service
	// additionally enforces caller-equals-subject so a logged-in wallet
	// cannot read another wallet's PnL.
	r.Route("/portfolio", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet)
		r.Get("/pnl/{wallet}", chatProxy.PortfolioPnl)
		r.Post("/refresh/{wallet}", chatProxy.PortfolioRefresh)
	})

	// Keep legacy /sessions routes for backward compatibility
	r.Route("/sessions", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Get("/", chatProxy.ListSessions)
		r.Post("/", chatProxy.CreateSession)
		// "/all" must come before "/{id}" so chi treats it as a literal.
		r.Delete("/all", chatProxy.DeleteAllSessions)
		r.Get("/{id}", chatProxy.GetSession)
		r.Delete("/{id}", chatProxy.DeleteSession)
		r.Get("/{id}/messages", chatProxy.GetMessages)
		r.Post("/{id}/messages", chatProxy.SendMessage)
		r.With(streamingTimeout).Get("/{id}/messages/stream", chatProxy.StreamMessages)
		r.Patch("/{id}/messages/{msgId}/metadata", chatProxy.PatchMessageMeta)
	})

	// Settings → Usage card: daily message/token counters + reset time.
	r.With(defaultTimeout).Get("/usage", chatProxy.Usage)

	// Settings → Privacy: delete long-term memories. Proxied to chat-service,
	// which forwards onto memory-service after wallet-scope authorisation.
	r.With(defaultTimeout).Delete("/user/memories", chatProxy.DeleteUserMemories)

	// Solana proxy — wallet auth required for all transaction-building routes
	solanaProxy := handlers.NewSolanaProxy(cfg.SolanaServiceHTTP, cfg.InternalAPIKey)
	r.Route("/actions", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet)
		r.Post("/quote", solanaProxy.PostQuote)
		r.Post("/build", solanaProxy.PostBuild)
		r.Post("/perp-execute", solanaProxy.PostPerpExecute)
		r.Post("/vanity-mint", solanaProxy.PostVanityMint)
		r.Post("/submit", solanaProxy.PostSubmit)
		r.Post("/simulate", solanaProxy.PostSimulate)
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
	r.Route("/validators", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet) // requires user auth (JWT)
		r.Get("/top", solanaProxy.GetTopValidators)
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
	// Token list is public data — no auth needed, served from Jupiter CDN with server-side caching.
	r.With(defaultTimeout).Get("/market/tokens/strict", marketProxy.GetTokenList)
	r.Route("/market", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet)
		r.Get("/prices", marketProxy.GetPrices)
		r.Get("/liquidity/multi", marketProxy.GetLiquidityMulti)
		r.Get("/tokens/search", marketProxy.SearchTokens)
		r.Get("/tokens/{mint}", marketProxy.GetTokenInfo)
		r.Get("/account/{wallet}/transactions", marketProxy.GetAccountTransactions)
		r.Get("/ohlcv", marketProxy.GetOHLCV)
		r.Get("/trending", marketProxy.GetTrending)
		r.Get("/analytics/{mint}", marketProxy.GetAnalytics)
		r.Get("/pairs/{mint}", marketProxy.GetPairs)
		r.Get("/trades/{mint}", marketProxy.GetTrades)
		r.Get("/holders/{mint}", marketProxy.GetHolders)
		r.Get("/wallet/pnl-summary", marketProxy.GetWalletPnlSummary)
		r.Get("/wallet/pnl-details", marketProxy.GetWalletPnlDetails)
		r.Get("/jito/tip-floor", marketProxy.GetJitoTipFloor)
		r.Get("/jito/tip-accounts", marketProxy.GetJitoTipAccounts)
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
		// Marinade real-time stats — proxied to avoid CORS, returns the live
		// msolPrice (mSOL/SOL spot rate) Marinade's own dashboard uses.
		r.Get("/marinade/exchange-rate", marketProxy.GetMarinadeExchangeRate)
		// JupSOL real-time conversion rate — Jupiter quote API probe so the
		// preview matches what the actual stake swap will pay out.
		r.Get("/jupsol/exchange-rate", marketProxy.GetJupSolExchangeRate)
		r.Get("/security/{mint}", marketProxy.GetSecurity)
		r.Get("/latest-pairs", marketProxy.GetLatestPairs)
		r.Get("/txns/{mint}", marketProxy.GetMintTransactions)
		r.Post("/helius/transactions", marketProxy.PostHeliusTransactions)
		// Jupiter Portfolio API — Jupiter products only (DCA, limit, perp, lend,
		// JUP/JupSOL stake, LP). x-api-key is injected server-side via
		// applyJupiterAuth so the key never reaches the browser.
		r.Get("/jupiter/portfolio/positions/{wallet}", marketProxy.GetJupiterPortfolioPositions)
		r.Get("/jupiter/portfolio/staked-jup/{wallet}", marketProxy.GetJupiterStakedJup)
		r.Get("/jupiter/portfolio/platforms", marketProxy.GetJupiterPortfolioPlatforms)
		// Pumpfun creator rewards — stub in PR 1; real on-chain decode lands in PR 3.
		r.Get("/pumpfun/rewards/{wallet}", marketProxy.GetPumpfunCreatorRewards)
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
	// Short-URL route for token metadata — keeps the on-chain URI ≤39 chars so it fits in a Solana tx.
	r.Get("/m/*", staticHandler.ServeMetadata)

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
	// Live DeFi yields (LST + lending) — real data for the frontend yield card.
	r.With(defaultTimeout, middleware.RequireWallet).Get("/yields", chatProxy.GetYields)

	return r
}

// internalKeyGate wraps h so that only requests carrying the correct
// X-Internal-Api-Key header are forwarded. Used to restrict /metrics to
// internal Prometheus scrapers. When key is empty (dev with no key set)
// the check is skipped so local development still works without configuration.
//
// During key rotation, OPRAI_INTERNAL_API_KEY_OLD lets the service accept
// the previous value too. Procedure:
//
//	1. Set OPRAI_INTERNAL_API_KEY_OLD = current OPRAI_INTERNAL_API_KEY on
//	   every service. Restart. Both keys now accepted.
//	2. Set OPRAI_INTERNAL_API_KEY = new value on every service. Restart. The
//	   new key is the primary; the old still validates inbound traffic.
//	3. Update the gateway to send the new key as well. Restart it.
//	4. After all caller traffic uses the new key, unset OPRAI_INTERNAL_API_KEY_OLD
//	   and restart services to retire the old key.
func internalKeyGate(key string, h http.Handler) http.Handler {
	prev := os.Getenv("OPRAI_INTERNAL_API_KEY_OLD")
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if key != "" {
			provided := r.Header.Get("X-Internal-Api-Key")
			if subtle.ConstantTimeCompare([]byte(provided), []byte(key)) != 1 &&
				(prev == "" || subtle.ConstantTimeCompare([]byte(provided), []byte(prev)) != 1) {
				http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
				return
			}
		}
		h.ServeHTTP(w, r)
	})
}
