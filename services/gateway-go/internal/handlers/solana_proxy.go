package handlers

import (
	"log/slog"
	"net/http"
	"net/http/httputil"
	"net/url"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/oprai/oprai/services/gateway-go/internal/middleware"
)

// SolanaProxy reverse-proxies REST requests to the solana service's HTTP server.
type SolanaProxy struct {
	proxy          *httputil.ReverseProxy
	internalAPIKey string
}

// NewSolanaProxy creates a new SolanaProxy that forwards requests to solanaServiceURL.
func NewSolanaProxy(solanaServiceURL string, internalAPIKey string) *SolanaProxy {
	target, err := url.Parse(solanaServiceURL)
	if err != nil {
		slog.Error("invalid solana service URL", "url", solanaServiceURL, "error", err)
		target, _ = url.Parse("http://localhost:3030")
	}

	rp := httputil.NewSingleHostReverseProxy(target)

	originalDirector := rp.Director
	rp.Director = func(req *http.Request) {
		originalDirector(req)
		// Strip any client-supplied X-User-Wallet, then re-inject the gateway-
		// validated wallet derived from the JWT claim. This prevents spoofing
		// while ensuring downstream services receive the authenticated wallet.
		req.Header.Del("X-User-Wallet")
		if wallet := middleware.GetWallet(req.Context()); wallet != "" {
			req.Header.Set("X-User-Wallet", wallet)
		}
		req.Header.Set("X-Internal-Api-Key", internalAPIKey)
		if reqID := chimiddleware.GetReqID(req.Context()); reqID != "" {
			req.Header.Set("X-Request-ID", reqID)
		}
	}

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
		slog.Error("solana proxy error", "error", err, "path", r.URL.Path)
		writeError(w, http.StatusBadGateway, "Solana service unavailable")
	}

	return &SolanaProxy{proxy: rp, internalAPIKey: internalAPIKey}
}

func (p *SolanaProxy) PostQuote(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/quote"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) PostBuild(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/build"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) PostSubmit(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/submit"
	p.proxy.ServeHTTP(w, r)
}

// PostVanityMint proxies the launch flow's request for a pre-ground "…pump"
// vanity mint keypair (or a random one if the backend pool is cold).
func (p *SolanaProxy) PostVanityMint(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/vanity-mint"
	p.proxy.ServeHTTP(w, r)
}

// PostClmmRangeCosts proxies POST /actions/clmm-range-costs — what each
// candidate price range costs to open, and whether the caller's wallet can
// pay it. Read-only: it opens nothing and signs nothing.
func (p *SolanaProxy) PostClmmRangeCosts(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/clmm-range-costs"
	p.proxy.ServeHTTP(w, r)
}

// PostPerpExecute proxies a user-signed Jupiter Perps transaction to the Solana
// service, which forwards it to Jupiter's execute endpoint (Jupiter adds the
// keeper signatures and submits). Perp txs are multi-signer and cannot be sent
// via plain RPC, so this is their dedicated submit path.
func (p *SolanaProxy) PostPerpExecute(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/perp-execute"
	p.proxy.ServeHTTP(w, r)
}

// PostSimulate proxies the frontend's defense-in-depth simulation fallback.
// The action card runs `connection.simulateTransaction` locally first; if the
// browser's RPC is misbehaving (404 / CORS / timeout) it falls back to this
// endpoint, which re-simulates server-side via the Solana service. Without
// it the frontend fails closed with a "sim:unavailable" error and the user
// can't sign — even when the build was perfectly valid.
func (p *SolanaProxy) PostSimulate(w http.ResponseWriter, r *http.Request) {
	slog.Info("solana proxy: forwarding /actions/simulate",
		"method", r.Method,
		"content_length", r.ContentLength,
		"has_body", r.Body != nil,
	)
	r.URL.Path = "/actions/simulate"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) ListProtocols(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/protocols"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) GetProtocol(w http.ResponseWriter, r *http.Request) {
	protocol := chi.URLParam(r, "protocol")
	r.URL.Path = "/protocols/" + protocol
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) ListTokens(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/tokens"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) GetToken(w http.ResponseWriter, r *http.Request) {
	symbol := chi.URLParam(r, "symbol")
	r.URL.Path = "/tokens/" + symbol
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) ListTransactions(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/transactions"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) GetTransaction(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	r.URL.Path = "/transactions/" + id
	p.proxy.ServeHTTP(w, r)
}

// CreateTransaction proxies POST /transactions — records a broadcast tx so the
// spending counter, tx history and the economics ledger get populated. Without
// this route the frontend's record call 405'd and nothing was ever stored.
func (p *SolanaProxy) CreateTransaction(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/transactions"
	p.proxy.ServeHTTP(w, r)
}

// UpdateTransactionStatus proxies PATCH /transactions/{id}/status — drives the
// submitted -> confirmed/failed transitions that finalize the economics ledger.
func (p *SolanaProxy) UpdateTransactionStatus(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	r.URL.Path = "/transactions/" + id + "/status"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) GetBalance(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/balance"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) GetLimitOrders(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/limit-orders"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) GetDcaOrders(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/dca-orders"
	p.proxy.ServeHTTP(w, r)
}

// GetRelayIntentStatus proxies GET /actions/relay/intent-status?requestId=… —
// the poll a cross-chain bridge card uses to learn whether the far side has
// settled. Without this route the poll 404'd and a bridge could never report
// success. The query string (requestId) rides along on r.URL.RawQuery.
func (p *SolanaProxy) GetRelayIntentStatus(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/relay/intent-status"
	p.proxy.ServeHTTP(w, r)
}

// PostRelayRecord proxies POST /actions/relay/record — books the economics of a
// settled Relay (EVM) swap so it feeds per-chain rewards. Fire-and-forget from
// the card; without this route it 404'd and no EVM swap was ever recorded.
func (p *SolanaProxy) PostRelayRecord(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/relay/record"
	p.proxy.ServeHTTP(w, r)
}

// Uniswap (same-chain EVM swap via the Trading API). Three hops: quote →
// (frontend signs the Permit2 permit) → swap calldata → send → record. The
// Uniswap API key lives in the solana-service, never the client, so all three
// go through here. Each is a 404 until listed in the router's explicit allowlist.
func (p *SolanaProxy) PostUniswapQuote(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/uniswap/quote"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) PostUniswapSwap(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/uniswap/swap"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) PostUniswapRecord(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/uniswap/record"
	p.proxy.ServeHTTP(w, r)
}

// PostUniswapLpBuild — build the approve + create txs for a Uniswap LP position.
func (p *SolanaProxy) PostUniswapLpBuild(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/uniswap/lp/build"
	p.proxy.ServeHTTP(w, r)
}

// PostUniswapLpBalances — the wallet's balance of a pool's two tokens.
func (p *SolanaProxy) PostUniswapLpBalances(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/uniswap/lp/balances"
	p.proxy.ServeHTTP(w, r)
}

// PostUniswapLpPositions — the wallet's Uniswap LP positions (V2/V3/V4, all chains).
func (p *SolanaProxy) PostUniswapLpPositions(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/uniswap/lp/positions"
	p.proxy.ServeHTTP(w, r)
}

// PostPoolsLaunchBuy — native pools.trade buy (trade.prepareBuy → tx to sign).
func (p *SolanaProxy) PostPoolsLaunchBuy(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/uniswap/launch/buy"
	p.proxy.ServeHTTP(w, r)
}

// PostPoolsLaunchSell — native pools.trade sell (trade.prepareSell → tx to sign).
func (p *SolanaProxy) PostPoolsLaunchSell(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/uniswap/launch/sell"
	p.proxy.ServeHTTP(w, r)
}

// PostPoolsLaunchCreate — prepare a pools.trade token launch (curve.prepareLaunch).
func (p *SolanaProxy) PostPoolsLaunchCreate(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/uniswap/launch/create"
	p.proxy.ServeHTTP(w, r)
}

// PostPoolsXAuthUrl — start pools.trade's real X OAuth (xVerification.getAuthUrl).
func (p *SolanaProxy) PostPoolsXAuthUrl(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/uniswap/launch/x-auth-url"
	p.proxy.ServeHTTP(w, r)
}

// PostPoolsEthBalance — a wallet's native ETH balance on Robinhood Chain.
func (p *SolanaProxy) PostPoolsEthBalance(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/actions/uniswap/eth-balance"
	p.proxy.ServeHTTP(w, r)
}

func (p *SolanaProxy) GetTopValidators(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/validators/top"
	p.proxy.ServeHTTP(w, r)
}
