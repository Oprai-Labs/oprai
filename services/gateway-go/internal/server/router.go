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
	r.Use(middleware.LoggerMiddleware(cfg.TrustProxyHeaders))
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

	// Browser CSP violation reports (report-uri target). Public, unauthenticated,
	// CSRF-exempt (see middleware/csrf.go) — the browser posts here with no
	// credentials. Logs each violation so a report-only policy can be verified.
	r.Post("/csp-report", handlers.CSPReport)

	// First-party image proxy for token/NFT logos (twimg/IPFS/arweave hosts get
	// blocked by browser tracker filters). Public GET, loaded via <img>, so no
	// auth/CSRF; SSRF-guarded inside the handler.
	imageProxy := handlers.NewImageProxy()
	r.Get("/img", imageProxy.Proxy)

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
		r.Get("/session", authProxy.GetSession) // used by frontend to restore session from cookie
		r.Post("/logout", authProxy.PostLogout) // clears HttpOnly cookie
		r.Get("/me", authProxy.GetMe)
	})
	r.Route("/account", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.With(middleware.RequireWallet).Get("/me", authProxy.GetAccountMe)
		r.With(middleware.RequireWallet).Post("/link/nonce", authProxy.PostAccountLinkNonce)
		r.With(middleware.RequireWallet).Post("/link/verify", authProxy.PostAccountLinkVerify)
		r.With(middleware.RequireWallet).Post("/link/evm/verify", authProxy.PostAccountLinkEVMVerify)
		r.With(middleware.RequireWallet).Post("/link/telegram", authProxy.PostAccountLinkTelegram)
		r.With(middleware.RequireWallet).Post("/link/twitter", authProxy.PostAccountLinkTwitter)
		r.With(middleware.RequireWallet).Post("/identity/{id}/primary", authProxy.PostAccountSetPrimary)
		r.With(middleware.RequireWallet).Delete("/identity/{id}", authProxy.DeleteAccountIdentity)
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

	// Product-analytics event ingest — funnel / feature / app-open events.
	// Wallet-gated; the chat proxy Director re-injects X-User-Wallet from the
	// validated JWT so a client can only ever log events for its own wallet.
	r.With(defaultTimeout, middleware.RequireWallet).Post("/events", chatProxy.PostEvents)

	// Rewards (tier / points / referral) — wallet-gated, served by chat-service.
	r.With(defaultTimeout, middleware.RequireWallet).Get("/me/rewards", chatProxy.GetRewards)
	r.With(defaultTimeout, middleware.RequireWallet).Post("/referral/redeem", chatProxy.RedeemReferral)
	r.With(defaultTimeout, middleware.RequireWallet).Post("/me/cashback/claim", chatProxy.ClaimCashback)

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

			// Share links. Owner-only — RequireWallet here rather than
			// leaving it to the chat-service, so an anonymous caller cannot
			// even probe whether a session id is shared.
			r.With(middleware.RequireWallet).Get("/{id}/share", chatProxy.GetSessionShare)
			r.With(middleware.RequireWallet).Post("/{id}/share", chatProxy.CreateSessionShare)
			r.With(middleware.RequireWallet).Delete("/{id}/share", chatProxy.RevokeSessionShare)
		})

		// The wallet's published links — backs the Shared Links page.
		r.With(defaultTimeout, middleware.RequireWallet).Get("/shares", chatProxy.ListShares)

		// PUBLIC: resolve a share token. Deliberately NOT wallet-gated — a
		// shared link is opened by people who are not signed in and never
		// will be, which is the entire feature. The token is the credential.
		// Read-only by construction: there is no public write route, and the
		// resolver returns messages with no session id or owner wallet
		// attached, so nothing here can be carried into the authed API.
		r.With(defaultTimeout).Get("/shared/{token}", chatProxy.GetPublicShare)

		// Help → Report Issue. Wallet-gated: a report is attributed to the
		// wallet that filed it, and the chat-service throttles per wallet —
		// neither works if anyone can post here anonymously.
		r.With(defaultTimeout, middleware.RequireWallet).Post("/issues", chatProxy.CreateIssueReport)
		r.With(defaultTimeout, middleware.RequireWallet).Get("/issues/mine", chatProxy.ListOwnIssueReports)

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
	r.With(defaultTimeout, middleware.RequireWallet).Get("/usage", chatProxy.Usage)

	// Settings → Privacy: delete long-term memories. Proxied to chat-service,
	// which forwards onto memory-service after wallet-scope authorisation.
	r.With(defaultTimeout, middleware.RequireWallet).Delete("/user/memories", chatProxy.DeleteUserMemories)

	// Solana proxy — wallet auth required for all transaction-building routes
	solanaProxy := handlers.NewSolanaProxy(cfg.SolanaServiceHTTP, cfg.InternalAPIKey)
	r.Route("/actions", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet)
		r.Post("/quote", solanaProxy.PostQuote)
		r.Post("/build", solanaProxy.PostBuild)
		r.Post("/perp-execute", solanaProxy.PostPerpExecute)
		r.Post("/vanity-mint", solanaProxy.PostVanityMint)
		// Costs the card reads before it offers a range. Every /actions path
		// is listed explicitly here, so a new one is a 404 until it is added.
		r.Post("/clmm-range-costs", solanaProxy.PostClmmRangeCosts)
		r.Post("/submit", solanaProxy.PostSubmit)
		r.Post("/simulate", solanaProxy.PostSimulate)
		r.Get("/limit-orders", solanaProxy.GetLimitOrders)
		r.Get("/dca-orders", solanaProxy.GetDcaOrders)
		// Relay (EVM) settlement: the bridge card polls intent-status to learn
		// the far side landed, and records the fill for per-chain rewards.
		r.Get("/relay/intent-status", solanaProxy.GetRelayIntentStatus)
		r.Post("/relay/record", solanaProxy.PostRelayRecord)
		// Uniswap same-chain EVM swap: quote → swap calldata → record.
		r.Post("/uniswap/quote", solanaProxy.PostUniswapQuote)
		r.Post("/uniswap/swap", solanaProxy.PostUniswapSwap)
		r.Post("/uniswap/record", solanaProxy.PostUniswapRecord)
		r.Post("/uniswap/lp/build", solanaProxy.PostUniswapLpBuild)
		r.Post("/uniswap/lp/balances", solanaProxy.PostUniswapLpBalances)
		r.Post("/uniswap/lp/positions", solanaProxy.PostUniswapLpPositions)
		r.Post("/uniswap/launch/buy", solanaProxy.PostPoolsLaunchBuy)
		r.Post("/uniswap/launch/sell", solanaProxy.PostPoolsLaunchSell)
		r.Post("/uniswap/launch/create", solanaProxy.PostPoolsLaunchCreate)
		r.Post("/uniswap/launch/x-auth-url", solanaProxy.PostPoolsXAuthUrl)
		r.Post("/uniswap/eth-balance", solanaProxy.PostPoolsEthBalance)
		r.Post("/uniswap/launch/token-meta", solanaProxy.PostPoolsTokenMeta)
		r.Post("/uniswap/launch/bid", solanaProxy.PostPoolsLaunchBid)
		r.Post("/pons/token-meta", solanaProxy.PostPonsTokenMeta)
		r.Post("/pons/buy", solanaProxy.PostPonsBuy)
		r.Post("/pons/sell", solanaProxy.PostPonsSell)
		r.Post("/pons/launch", solanaProxy.PostPonsLaunch)
		r.Post("/morpho/supply", solanaProxy.PostMorphoSupply)
		r.Post("/morpho/borrow", solanaProxy.PostMorphoBorrow)
		r.Post("/morpho/repay", solanaProxy.PostMorphoRepay)
		r.Post("/morpho/withdraw", solanaProxy.PostMorphoWithdraw)
		r.Post("/sushi/swap", solanaProxy.PostSushiSwap)
		r.Post("/sushi/add-liquidity", solanaProxy.PostSushiAddLiquidity)
		r.Post("/sushi/liquidity-quote", solanaProxy.PostSushiLiquidityQuote)
		r.Post("/opensea/buy", solanaProxy.PostOpenseaBuy)
		r.Post("/opensea/accept-offer", solanaProxy.PostOpenseaAcceptOffer)
		r.Post("/opensea/list", solanaProxy.PostOpenseaList)
		r.Post("/opensea/make-offer", solanaProxy.PostOpenseaMakeOffer)
		r.Post("/opensea/order/submit", solanaProxy.PostOpenseaOrderSubmit)
		// Lighter perps (Robinhood Chain domain) — proxied to chat-service
		// (Python SDK + agent-key store), NOT solana-service. Wallet auth
		// already applied by the block-level RequireWallet above.
		r.Post("/lighter/onboard/build", chatProxy.LighterProxy)
		r.Post("/lighter/onboard/submit", chatProxy.LighterProxy)
		r.Post("/lighter/deposit/build", chatProxy.LighterProxy)
		r.Post("/lighter/open", chatProxy.LighterProxy)
		r.Post("/lighter/close", chatProxy.LighterProxy)
		r.Post("/lighter/leverage", chatProxy.LighterProxy)
		r.Post("/lighter/withdraw", chatProxy.LighterProxy)
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
		r.Post("/", solanaProxy.CreateTransaction) // record a broadcast tx
		r.Get("/{id}", solanaProxy.GetTransaction)
		r.Patch("/{id}/status", solanaProxy.UpdateTransactionStatus) // submitted->confirmed/failed
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
		r.Use(middleware.RequireWallet)
		r.Get("/", memoryProxy.GetMemories)
		r.Post("/", memoryProxy.StoreMemory)
		r.Delete("/", memoryProxy.ClearMemories)
		r.Get("/search", memoryProxy.SearchMemories)
		r.Delete("/{id}", memoryProxy.DeleteMemoryById)
	})
	r.Route("/consent", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet)
		r.Get("/", memoryProxy.GetConsent)
		r.Put("/", memoryProxy.UpdateConsent)
	})
	r.With(defaultTimeout, middleware.RequireWallet).Post("/summarize", memoryProxy.Summarize)

	// Market data proxy — external APIs (Jupiter, DexScreener, Birdeye).
	// RequireWallet prevents unauthenticated callers from exhausting paid API quotas
	// (Birdeye, Helius, Jupiter keys are kept server-side).
	marketProxy := handlers.NewMarketProxy(ctx, cfg.BirdeyeAPIKey, cfg.JupiterAPIKey, cfg.HeliusAPIKey, cfg.AlchemyAPIKey, cfg.MoralisAPIKey, cfg.CORSOrigin)
	// Token list is public data — no auth needed, served from Jupiter CDN with server-side caching.
	r.With(defaultTimeout).Get("/market/tokens/strict", marketProxy.GetTokenList)
	// Which non-Solana chains we read wallets on. A description of ourselves —
	// no upstream call, no key, nothing to exhaust — and what /help is built
	// from, so it must be reachable without a wallet.
	r.With(defaultTimeout).Get("/market/evm/chains", marketProxy.GetEvmChains)
	// Lighter markets — public market data (symbol list + live prices), served by
	// chat-service. No wallet: the trade card fetches it for the picker + 3s price
	// poll before the user has even connected.
	r.With(defaultTimeout).Get("/market/lighter/markets", chatProxy.LighterProxy)
	// Lighter account/positions — served by chat-service (Python SDK), not the
	// market proxy. Wallet-gated: reads the caller's own linked EVM account.
	r.With(defaultTimeout, middleware.RequireWallet).Get("/market/lighter/account", chatProxy.LighterProxy)
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
		// EVM wallet holdings (Alchemy): GET /market/evm/portfolio?address=0x..
		r.Get("/evm/portfolio", marketProxy.GetEvmPortfolio)
		// EVM DeFi positions (Moralis): GET /market/evm/positions?address=0x..
		r.Get("/evm/positions", marketProxy.GetEvmPositions)
		// EVM tx history + platform labels (Moralis): GET /market/evm/transactions?address=0x..
		r.Get("/evm/transactions", marketProxy.GetEvmTransactions)
		// EVM NFTs (Alchemy): GET /market/evm/nfts?address=0x..
		r.Get("/evm/nfts", marketProxy.GetEvmNfts)
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

	// Solana JSON-RPC proxy — keeps the Helius API key server-side. NOT
	// wallet-gated: browser web3.js Connections can't reliably attach the auth
	// cookie/JWT to their fetches, and gating this behind RequireWallet
	// intermittently 401'd legitimate reads (blockhash, account info) and tx
	// submits, freezing wallet flows. Broadcasting a signed tx and reading
	// public chain state need no auth anyway. Abuse is bounded by the CSRF
	// (X-Requested-With) check + global rate limiting; the Helius key stays
	// server-side.
	r.With(defaultTimeout).Post("/rpc", marketProxy.PostRpc)

	// Server-side token metadata resolver (name/symbol/logo via Helius getAsset,
	// batched + cached). Root-level + NOT wallet-gated (the /market group is
	// RequireWallet, which is exactly why client metadata resolution was flaky).
	// Same rationale as /rpc: public on-chain metadata, Helius key stays server-side.
	r.With(defaultTimeout).Post("/token-meta", marketProxy.PostTokenMeta)

	// Token-logo cache. Public and un-gated for the same reason as the two
	// above, and because an <img> can't send our auth headers at all.
	r.With(defaultTimeout).Get("/token-image", marketProxy.GetTokenImage)

	// Upload handler — stores files locally, serves via /uploads/*
	//
	// Check the directory now rather than on someone's first upload. It was
	// unwritable in production for as long as the feature had existed, and the
	// only report was a 500 buried in the request log — by which point a user
	// was mid-launch and the frontend quietly carried on with a URL that
	// pointed at their own browser.
	handlers.CheckUploadDirWritable(cfg.UploadDir)
	uploadHandler := handlers.NewUploadHandler(cfg.UploadDir, cfg.PublicBaseURL)
	r.Route("/upload", func(r chi.Router) {
		r.Use(defaultTimeout)
		r.Use(middleware.RequireWallet)
		r.Post("/image", uploadHandler.UploadImage)
		r.Post("/metadata", uploadHandler.UploadMetadata)
		// Server-side because the browser cannot: pump.fun's IPFS endpoint
		// sends no CORS headers, so the direct call always failed.
		r.Post("/pumpfun-ipfs", uploadHandler.UploadToPumpFunIPFS)
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
//  1. Set OPRAI_INTERNAL_API_KEY_OLD = current OPRAI_INTERNAL_API_KEY on
//     every service. Restart. Both keys now accepted.
//  2. Set OPRAI_INTERNAL_API_KEY = new value on every service. Restart. The
//     new key is the primary; the old still validates inbound traffic.
//  3. Update the gateway to send the new key as well. Restart it.
//  4. After all caller traffic uses the new key, unset OPRAI_INTERNAL_API_KEY_OLD
//     and restart services to retire the old key.
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
