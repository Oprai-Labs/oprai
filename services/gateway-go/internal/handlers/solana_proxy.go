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

func (p *SolanaProxy) GetTopValidators(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/validators/top"
	p.proxy.ServeHTTP(w, r)
}
