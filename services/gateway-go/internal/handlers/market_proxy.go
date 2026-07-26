package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
)

// solanaAddrRe matches valid Solana base-58 encoded public keys / mint addresses.
// Solana addresses are 32–44 characters using the Bitcoin base-58 alphabet
// (no 0, O, I or l).
var solanaAddrRe = regexp.MustCompile(`^[1-9A-HJ-NP-Za-km-z]{32,44}$`)

// isValidSolanaAddress returns true when addr looks like a valid Solana public
// key / mint address. Used to reject clearly-malformed inputs before they reach
// external APIs or get interpolated into request payloads.
func isValidSolanaAddress(addr string) bool {
	return solanaAddrRe.MatchString(addr)
}

// Simple in-memory TTL cache (will be replaced with Redis later)
type cacheEntry struct {
	data      []byte
	expiresAt time.Time
}

type memCache struct {
	mu    sync.RWMutex
	items map[string]cacheEntry
}

func newMemCache(ctx context.Context) *memCache {
	c := &memCache{items: make(map[string]cacheEntry)}
	go c.cleanup(ctx)
	return c
}

func (c *memCache) Get(key string) ([]byte, bool) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	entry, ok := c.items[key]
	if !ok || time.Now().After(entry.expiresAt) {
		return nil, false
	}
	return entry.data, true
}

func (c *memCache) Set(key string, data []byte, ttl time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.items[key] = cacheEntry{data: data, expiresAt: time.Now().Add(ttl)}
}

func (c *memCache) cleanup(ctx context.Context) {
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			c.mu.Lock()
			now := time.Now()
			for k, v := range c.items {
				if now.After(v.expiresAt) {
					delete(c.items, k)
				}
			}
			c.mu.Unlock()
		}
	}
}

// MarketProxy handles proxied market data requests to external APIs.
type MarketProxy struct {
	birdeyeAPIKey string
	jupiterAPIKey string
	heliusAPIKey  string
	cache         *memCache
	client        *http.Client
}

// NewMarketProxy creates a new MarketProxy with the given API keys.
// ctx is the application root context; the cache cleanup goroutine stops when
// ctx is cancelled.
func NewMarketProxy(ctx context.Context, birdeyeAPIKey, jupiterAPIKey, heliusAPIKey string) *MarketProxy {
	return &MarketProxy{
		birdeyeAPIKey: birdeyeAPIKey,
		jupiterAPIKey: jupiterAPIKey,
		heliusAPIKey:  heliusAPIKey,
		cache:         newMemCache(ctx),
		client: &http.Client{
			Timeout: 15 * time.Second,
		},
	}
}

// jupiterHost picks the right Jupiter base host for the keyed-vs-public split:
//   - `api.jup.ag`      — paid, requires `x-api-key` header
//   - `lite-api.jup.ag` — public/free, rate-limited, no key
//
// Both serve the same paths under `/price`, `/tokens/v2`, `/swap/v1` etc.
// Per Jupiter docs (https://dev.jup.ag/docs/), keyed traffic must hit the
// paid host or the request is anonymous and rate-limited even with a key.
func (m *MarketProxy) jupiterHost() string {
	if m.jupiterAPIKey != "" {
		return "https://api.jup.ag"
	}
	return "https://lite-api.jup.ag"
}

// applyJupiterAuth attaches `x-api-key` when present. Always called on outbound
// Jupiter requests so we don't have to remember per-handler.
func (m *MarketProxy) applyJupiterAuth(req *http.Request) {
	if m.jupiterAPIKey != "" {
		req.Header.Set("x-api-key", m.jupiterAPIKey)
	}
}

// Cache TTLs
const (
	priceCacheTTL       = 10 * time.Second
	tokenCacheTTL       = 30 * time.Minute
	ohlcvCacheTTL       = 60 * time.Second
	trendingCacheTTL    = 60 * time.Second
	analyticsCacheTTL   = 2 * time.Minute
	pairsCacheTTL       = 60 * time.Second
	searchCacheTTL      = 5 * time.Minute
	tradesCacheTTL      = 15 * time.Second
	tokenMetaCacheTTL   = 30 * time.Minute
	holdersCacheTTL     = 2 * time.Minute
	securityCacheTTL    = 5 * time.Minute
	latestPairsCacheTTL = 30 * time.Second
	txnsCacheTTL        = 30 * time.Second
	accountTxCacheTTL   = 10 * time.Second
)

const lamportsPerSOL = 1_000_000_000

// GetPrices proxies GET /market/prices?ids={mints} to Jupiter Price API v3.
func (m *MarketProxy) GetPrices(w http.ResponseWriter, r *http.Request) {
	ids := r.URL.Query().Get("ids")
	if ids == "" {
		writeError(w, http.StatusBadRequest, "ids parameter required")
		return
	}

	cacheKey := "prices:" + ids
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	apiURL := fmt.Sprintf("%s/price/v3?ids=%s", m.jupiterHost(), ids)
	req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	m.applyJupiterAuth(req)

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("jupiter price API error", "error", err)
		writeError(w, http.StatusBadGateway, "price service unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read price response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, priceCacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// SearchTokens proxies GET /market/tokens/search?q={query} to Jupiter Token API.
func (m *MarketProxy) SearchTokens(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query().Get("q")
	if query == "" {
		writeError(w, http.StatusBadRequest, "q parameter required")
		return
	}

	limit := r.URL.Query().Get("limit")
	if limit == "" {
		limit = "20"
	}

	cacheKey := "token-search:" + strings.ToLower(query) + ":" + limit
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	apiURL := fmt.Sprintf("%s/tokens/v2/search?query=%s&limit=%s", m.jupiterHost(), query, limit)
	req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	m.applyJupiterAuth(req)

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("jupiter token search error", "error", err)
		writeError(w, http.StatusBadGateway, "token search unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read token response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, searchCacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetTokenList proxies GET /market/tokens/strict to Jupiter's verified token
// list. The legacy `token.jup.ag/strict` host was retired by Jupiter, manifesting
// client-side as a 502 toast spam loop. The current official V2 endpoint is
// `/tokens/v2/tag?query=verified` on either the keyed (`api.jup.ag`) or public
// (`lite-api.jup.ag`) host. V2 response shape uses `id` for address and `icon`
// for logo — `token-registry.service.ts:79-91` already handles both via
// `?? id` and `?? icon` fallbacks, so this is a drop-in replacement.
func (m *MarketProxy) GetTokenList(w http.ResponseWriter, r *http.Request) {
	cacheKey := "token-list:strict"
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	apiURL := m.jupiterHost() + "/tokens/v2/tag?query=verified"
	req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	m.applyJupiterAuth(req)

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("jupiter token list error", "error", err)
		writeError(w, http.StatusBadGateway, "token list unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read token list")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, tokenCacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetTokenInfo proxies GET /market/tokens/{mint} to Jupiter token API.
func (m *MarketProxy) GetTokenInfo(w http.ResponseWriter, r *http.Request) {
	mint := chi.URLParam(r, "mint")
	if mint == "" {
		writeError(w, http.StatusBadRequest, "mint parameter required")
		return
	}
	if !isValidSolanaAddress(mint) {
		writeError(w, http.StatusBadRequest, "invalid mint address")
		return
	}

	cacheKey := "token-info:" + mint
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	apiURL := fmt.Sprintf("%s/tokens/v2/search?query=%s&limit=1", m.jupiterHost(), mint)
	req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	m.applyJupiterAuth(req)

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("jupiter token info error", "error", err)
		writeError(w, http.StatusBadGateway, "token info unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read token info")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, tokenCacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetLiquidityMulti proxies GET /market/liquidity/multi?ids=mint1,mint2,...
// to Birdeye `/defi/v3/token/market-data/multiple`. Used by the token-picker
// modal to render a liquidity badge per token row so the user can see which
// targets are tradable BEFORE they pick. Returning the raw Birdeye payload
// (which includes price, liquidity, market_cap, fdv per token) keeps the
// proxy generic; frontend pulls just the liquidity field for the badge.
// Cap of 50 mints per call mirrors Birdeye's documented batch limit.
func (m *MarketProxy) GetLiquidityMulti(w http.ResponseWriter, r *http.Request) {
	ids := r.URL.Query().Get("ids")
	if ids == "" {
		writeError(w, http.StatusBadRequest, "ids parameter required")
		return
	}
	if m.birdeyeAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "birdeye API key not configured")
		return
	}
	// Validate every mint individually so a single bad ID can't poison the
	// upstream call. Also caps the request size (50 is Birdeye's batch limit).
	mints := strings.Split(ids, ",")
	if len(mints) == 0 || len(mints) > 50 {
		writeError(w, http.StatusBadRequest, "ids must contain 1–50 comma-separated mints")
		return
	}
	for _, mint := range mints {
		if !isValidSolanaAddress(strings.TrimSpace(mint)) {
			writeError(w, http.StatusBadRequest, "invalid mint in ids list")
			return
		}
	}

	cacheKey := "liquidity-multi:" + ids
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	apiURL := fmt.Sprintf("https://public-api.birdeye.so/defi/v3/token/market-data/multiple?list_address=%s",
		url.QueryEscape(ids))
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	req.Header.Set("X-API-KEY", m.birdeyeAPIKey)
	req.Header.Set("x-chain", "solana")

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("birdeye liquidity-multi error", "error", err)
		writeError(w, http.StatusBadGateway, "liquidity data unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read liquidity response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		// 5-minute TTL — liquidity moves slowly enough that the picker can
		// reuse the badge across rapid open/close cycles without hammering
		// Birdeye. Token-list won't change often during a chat session.
		m.cache.Set(cacheKey, body, 5*time.Minute)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetOHLCV proxies GET /market/ohlcv?address={mint}&type={interval}&time_from={}&time_to={} to Birdeye.
func (m *MarketProxy) GetOHLCV(w http.ResponseWriter, r *http.Request) {
	address := r.URL.Query().Get("address")
	if address == "" {
		writeError(w, http.StatusBadRequest, "address parameter required")
		return
	}
	if !isValidSolanaAddress(address) {
		writeError(w, http.StatusBadRequest, "invalid address")
		return
	}

	intervalType := r.URL.Query().Get("type")
	if intervalType == "" {
		intervalType = "15m"
	}
	timeFrom := r.URL.Query().Get("time_from")
	timeTo := r.URL.Query().Get("time_to")

	cacheKey := fmt.Sprintf("ohlcv:%s:%s:%s:%s", address, intervalType, timeFrom, timeTo)
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	apiURL := fmt.Sprintf("https://public-api.birdeye.so/defi/ohlcv?address=%s&type=%s", address, intervalType)
	if timeFrom != "" {
		apiURL += "&time_from=" + timeFrom
	}
	if timeTo != "" {
		apiURL += "&time_to=" + timeTo
	}

	req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	req.Header.Set("X-API-KEY", m.birdeyeAPIKey)
	req.Header.Set("x-chain", "solana")

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("birdeye OHLCV error", "error", err)
		writeError(w, http.StatusBadGateway, "chart data unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read OHLCV response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, ohlcvCacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetTrending proxies GET /market/trending to DexScreener boosted tokens.
func (m *MarketProxy) GetTrending(w http.ResponseWriter, r *http.Request) {
	cacheKey := "trending:solana"
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	// Fetch both boosted and top traders in parallel
	type result struct {
		key  string
		data json.RawMessage
	}

	results := make(map[string]json.RawMessage)
	var mu sync.Mutex
	var wg sync.WaitGroup

	endpoints := map[string]string{
		"boosted":  "https://api.dexscreener.com/token-boosts/top/v1",
		"trending": "https://api.dexscreener.com/token-profiles/latest/v1",
	}

	for key, url := range endpoints {
		wg.Add(1)
		go func(k, u string) {
			defer wg.Done()
			req, err := http.NewRequestWithContext(r.Context(), "GET", u, nil)
			if err != nil {
				return
			}
			resp, err := m.client.Do(req)
			if err != nil {
				slog.Error("dexscreener trending error", "endpoint", k, "error", err)
				return
			}
			defer resp.Body.Close()
			body, err := io.ReadAll(resp.Body)
			if err != nil {
				return
			}
			mu.Lock()
			results[k] = json.RawMessage(body)
			mu.Unlock()
		}(key, url)
	}
	wg.Wait()

	combined, _ := json.Marshal(results)
	m.cache.Set(cacheKey, combined, trendingCacheTTL)

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.Write(combined)
}

// GetAnalytics proxies GET /market/analytics/{mint} to DexScreener + Birdeye composite.
func (m *MarketProxy) GetAnalytics(w http.ResponseWriter, r *http.Request) {
	mint := chi.URLParam(r, "mint")
	if mint == "" {
		writeError(w, http.StatusBadRequest, "mint parameter required")
		return
	}
	if !isValidSolanaAddress(mint) {
		writeError(w, http.StatusBadRequest, "invalid mint address")
		return
	}

	cacheKey := "analytics:" + mint
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	// Fetch from DexScreener and Birdeye in parallel
	type apiResult struct {
		source string
		data   json.RawMessage
	}
	ch := make(chan apiResult, 2)

	// DexScreener token data
	go func() {
		apiURL := fmt.Sprintf("https://api.dexscreener.com/tokens/v1/solana/%s", mint)
		req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
		if err != nil {
			ch <- apiResult{source: "dexscreener"}
			return
		}
		resp, err := m.client.Do(req)
		if err != nil {
			slog.Error("dexscreener analytics error", "error", err)
			ch <- apiResult{source: "dexscreener"}
			return
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		ch <- apiResult{source: "dexscreener", data: json.RawMessage(body)}
	}()

	// Birdeye token overview
	go func() {
		apiURL := fmt.Sprintf("https://public-api.birdeye.so/defi/token_overview?address=%s", mint)
		req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
		if err != nil {
			ch <- apiResult{source: "birdeye"}
			return
		}
		req.Header.Set("X-API-KEY", m.birdeyeAPIKey)
		req.Header.Set("x-chain", "solana")
		resp, err := m.client.Do(req)
		if err != nil {
			slog.Error("birdeye analytics error", "error", err)
			ch <- apiResult{source: "birdeye"}
			return
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		ch <- apiResult{source: "birdeye", data: json.RawMessage(body)}
	}()

	composite := make(map[string]json.RawMessage)
	for i := 0; i < 2; i++ {
		res := <-ch
		if res.data != nil {
			composite[res.source] = res.data
		}
	}

	combined, _ := json.Marshal(composite)
	m.cache.Set(cacheKey, combined, analyticsCacheTTL)

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.Write(combined)
}

// GetPairs proxies GET /market/pairs/{mint} to DexScreener pairs.
func (m *MarketProxy) GetPairs(w http.ResponseWriter, r *http.Request) {
	mint := chi.URLParam(r, "mint")
	if mint == "" {
		writeError(w, http.StatusBadRequest, "mint parameter required")
		return
	}
	if !isValidSolanaAddress(mint) {
		writeError(w, http.StatusBadRequest, "invalid mint address")
		return
	}

	cacheKey := "pairs:" + mint
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	apiURL := fmt.Sprintf("https://api.dexscreener.com/tokens/v1/solana/%s", mint)
	req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("dexscreener pairs error", "error", err)
		writeError(w, http.StatusBadGateway, "pairs data unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read pairs response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, pairsCacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetWalletPnlSummary proxies GET /market/wallet/pnl-summary?wallet=<>&duration=<>
// to Birdeye `/wallet/v2/pnl/summary`. duration ∈ {all, 90d, 30d, 7d, 24h}.
// Validates the wallet address shape so we never fan out malformed traffic.
func (m *MarketProxy) GetWalletPnlSummary(w http.ResponseWriter, r *http.Request) {
	wallet := r.URL.Query().Get("wallet")
	duration := r.URL.Query().Get("duration")
	if wallet == "" {
		writeError(w, http.StatusBadRequest, "wallet parameter required")
		return
	}
	if !isValidSolanaAddress(wallet) {
		writeError(w, http.StatusBadRequest, "invalid wallet address")
		return
	}
	if duration == "" {
		duration = "30d"
	}
	switch duration {
	case "all", "90d", "30d", "7d", "24h":
	default:
		writeError(w, http.StatusBadRequest, "duration must be one of: all, 90d, 30d, 7d, 24h")
		return
	}
	if m.birdeyeAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "birdeye API key not configured")
		return
	}

	cacheKey := fmt.Sprintf("pnl-summary:%s:%s", wallet, duration)
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	apiURL := fmt.Sprintf("https://public-api.birdeye.so/wallet/v2/pnl/summary?wallet=%s&duration=%s",
		url.QueryEscape(wallet), url.QueryEscape(duration))
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	req.Header.Set("X-API-KEY", m.birdeyeAPIKey)
	req.Header.Set("x-chain", "solana")

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("birdeye pnl-summary error", "error", err)
		writeError(w, http.StatusBadGateway, "pnl summary unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read pnl summary response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, 2*time.Minute)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetWalletPnlDetails proxies GET /market/wallet/pnl-details?wallet=<>&duration=<>&limit=<>
// to Birdeye `/wallet/v2/pnl/details` (POST upstream). limit defaults to 10, capped at 50.
func (m *MarketProxy) GetWalletPnlDetails(w http.ResponseWriter, r *http.Request) {
	wallet := r.URL.Query().Get("wallet")
	duration := r.URL.Query().Get("duration")
	limitStr := r.URL.Query().Get("limit")
	if wallet == "" {
		writeError(w, http.StatusBadRequest, "wallet parameter required")
		return
	}
	if !isValidSolanaAddress(wallet) {
		writeError(w, http.StatusBadRequest, "invalid wallet address")
		return
	}
	if duration == "" {
		duration = "30d"
	}
	switch duration {
	case "all", "90d", "30d", "7d", "24h":
	default:
		writeError(w, http.StatusBadRequest, "duration must be one of: all, 90d, 30d, 7d, 24h")
		return
	}
	limit := 10
	if limitStr != "" {
		if v, err := strconv.Atoi(limitStr); err == nil && v > 0 && v <= 50 {
			limit = v
		}
	}
	if m.birdeyeAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "birdeye API key not configured")
		return
	}

	cacheKey := fmt.Sprintf("pnl-details:%s:%s:%d", wallet, duration, limit)
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	payload, _ := json.Marshal(map[string]any{
		"wallet":    wallet,
		"duration":  duration,
		"sort_type": "desc",
		"sort_by":   "total_pnl",
		"limit":     limit,
	})
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost,
		"https://public-api.birdeye.so/wallet/v2/pnl/details", bytes.NewReader(payload))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	req.Header.Set("X-API-KEY", m.birdeyeAPIKey)
	req.Header.Set("x-chain", "solana")
	req.Header.Set("Content-Type", "application/json")

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("birdeye pnl-details error", "error", err)
		writeError(w, http.StatusBadGateway, "pnl details unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read pnl details response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, 2*time.Minute)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetTrades proxies GET /market/trades/{mint} to Birdeye token transactions.
func (m *MarketProxy) GetTrades(w http.ResponseWriter, r *http.Request) {
	mint := chi.URLParam(r, "mint")
	if mint == "" {
		writeError(w, http.StatusBadRequest, "mint parameter required")
		return
	}
	if !isValidSolanaAddress(mint) {
		writeError(w, http.StatusBadRequest, "invalid mint address")
		return
	}

	cacheKey := "trades:" + mint
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	apiURL := fmt.Sprintf("https://public-api.birdeye.so/defi/txs/token?address=%s&limit=50&sort_type=desc", mint)
	req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	req.Header.Set("X-API-KEY", m.birdeyeAPIKey)
	req.Header.Set("x-chain", "solana")

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("birdeye trades error", "error", err)
		writeError(w, http.StatusBadGateway, "trades data unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read trades response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, tradesCacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetHolders proxies GET /market/holders/{mint} to Helius getTokenAccounts.
func (m *MarketProxy) GetHolders(w http.ResponseWriter, r *http.Request) {
	mint := chi.URLParam(r, "mint")
	if mint == "" {
		writeError(w, http.StatusBadRequest, "mint parameter required")
		return
	}
	if !isValidSolanaAddress(mint) {
		writeError(w, http.StatusBadRequest, "invalid mint address")
		return
	}

	cacheKey := "holders:" + mint
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	if m.heliusAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "helius API key not configured")
		return
	}

	// Build the JSON-RPC payload with json.Marshal to prevent injection if the
	// mint ever contained characters that could break a raw string interpolation.
	type rpcOptions struct {
		ShowZeroBalance bool `json:"showZeroBalance"`
	}
	type rpcParams struct {
		Mint    string     `json:"mint"`
		Limit   int        `json:"limit"`
		Options rpcOptions `json:"options"`
	}
	type rpcRequest struct {
		JSONRPC string    `json:"jsonrpc"`
		ID      int       `json:"id"`
		Method  string    `json:"method"`
		Params  rpcParams `json:"params"`
	}
	payloadBytes, err := json.Marshal(rpcRequest{
		JSONRPC: "2.0",
		ID:      1,
		Method:  "getTokenAccounts",
		Params: rpcParams{
			Mint:    mint,
			Limit:   20,
			Options: rpcOptions{ShowZeroBalance: false},
		},
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to build request")
		return
	}

	req, err := http.NewRequestWithContext(r.Context(), "POST", "https://mainnet.helius-rpc.com", bytes.NewReader(payloadBytes))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+m.heliusAPIKey)

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("helius holders error", "error", err)
		writeError(w, http.StatusBadGateway, "holders data unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read holders response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, holdersCacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetSecurity proxies GET /market/security/{mint} to RugCheck token report.
func (m *MarketProxy) GetSecurity(w http.ResponseWriter, r *http.Request) {
	mint := chi.URLParam(r, "mint")
	if mint == "" {
		writeError(w, http.StatusBadRequest, "mint parameter required")
		return
	}
	if !isValidSolanaAddress(mint) {
		writeError(w, http.StatusBadRequest, "invalid mint address")
		return
	}

	cacheKey := "security:" + mint
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	apiURL := fmt.Sprintf("https://api.rugcheck.xyz/v1/tokens/%s/report/summary", mint)
	req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("rugcheck security error", "error", err)
		writeError(w, http.StatusBadGateway, "security data unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read security response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, securityCacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetLatestPairs proxies GET /market/latest-pairs to DexScreener latest Solana pairs.
func (m *MarketProxy) GetLatestPairs(w http.ResponseWriter, r *http.Request) {
	cacheKey := "latest-pairs:solana"
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	apiURL := "https://api.dexscreener.com/latest/dex/pairs/solana"
	req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("dexscreener latest pairs error", "error", err)
		writeError(w, http.StatusBadGateway, "latest pairs unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read latest pairs response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, latestPairsCacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetMintTransactions proxies GET /market/txns/{mint} to Helius enhanced transactions.
func (m *MarketProxy) GetMintTransactions(w http.ResponseWriter, r *http.Request) {
	mint := chi.URLParam(r, "mint")
	if mint == "" {
		writeError(w, http.StatusBadRequest, "mint parameter required")
		return
	}
	if !isValidSolanaAddress(mint) {
		writeError(w, http.StatusBadRequest, "invalid mint address")
		return
	}

	cacheKey := "txns:" + mint
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	if m.heliusAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "helius API key not configured")
		return
	}

	apiURL := fmt.Sprintf("https://api.helius.xyz/v0/addresses/%s/transactions?limit=50", mint)
	req, err := http.NewRequestWithContext(r.Context(), "GET", apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	req.Header.Set("Authorization", "Bearer "+m.heliusAPIKey)

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("helius transactions error", "error", err)
		writeError(w, http.StatusBadGateway, "transaction data unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read transactions response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, txnsCacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

type heliusTransfer struct {
	Amount          int64  `json:"amount"`
	FromUserAccount string `json:"fromUserAccount"`
	ToUserAccount   string `json:"toUserAccount"`
}

type heliusTx struct {
	Signature        string           `json:"signature"`
	Timestamp        int64            `json:"timestamp"`
	Type             string           `json:"type"`
	Source           string           `json:"source"`
	Description      string           `json:"description"`
	Fee              int64            `json:"fee"`
	TransactionError any              `json:"transactionError"`
	NativeTransfers  []heliusTransfer `json:"nativeTransfers"`
	Instructions     []struct {
		ProgramID string `json:"programId"`
	} `json:"instructions"`
	InnerInstructions []struct {
		Instructions []struct {
			ProgramID string `json:"programId"`
		} `json:"instructions"`
	} `json:"innerInstructions"`
}

type accountTxItem struct {
	Signature   string   `json:"signature"`
	BlockTime   int64    `json:"blockTime"`
	Success     bool     `json:"success"`
	Type        string   `json:"type"`
	Platform    string   `json:"platform"`
	Programs    []string `json:"programs"`
	Description string   `json:"description"`
	ValueSol    float64  `json:"valueSol"`
	FeeLamports int64    `json:"feeLamports"`
	FeeSol      float64  `json:"feeSol"`
}

func parseBoolParam(raw string, fallback bool) bool {
	if raw == "" {
		return fallback
	}
	v, err := strconv.ParseBool(raw)
	if err != nil {
		return fallback
	}
	return v
}

func parseIntParam(raw string, fallback, min, max int) int {
	if raw == "" {
		return fallback
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	if v < min {
		return min
	}
	if v > max {
		return max
	}
	return v
}

func splitCSVLower(raw string) map[string]struct{} {
	out := make(map[string]struct{})
	if raw == "" {
		return out
	}
	for _, part := range strings.Split(raw, ",") {
		item := strings.ToLower(strings.TrimSpace(part))
		if item == "" {
			continue
		}
		out[item] = struct{}{}
	}
	return out
}

func timeRangeCutoff(rangeKey string, now time.Time) int64 {
	switch strings.ToLower(strings.TrimSpace(rangeKey)) {
	case "24h":
		return now.Add(-24 * time.Hour).Unix()
	case "7d":
		return now.AddDate(0, 0, -7).Unix()
	case "30d":
		return now.AddDate(0, 0, -30).Unix()
	default:
		return 0
	}
}

func normalizeHeliusTx(tx heliusTx, walletLower string) accountTxItem {
	netLamports := int64(0)
	for _, transfer := range tx.NativeTransfers {
		from := strings.ToLower(strings.TrimSpace(transfer.FromUserAccount))
		to := strings.ToLower(strings.TrimSpace(transfer.ToUserAccount))
		if from == walletLower {
			netLamports -= transfer.Amount
		}
		if to == walletLower {
			netLamports += transfer.Amount
		}
	}

	programs := inferPrograms(tx)

	return accountTxItem{
		Signature:   tx.Signature,
		BlockTime:   tx.Timestamp,
		Success:     tx.TransactionError == nil,
		Type:        strings.ToLower(strings.TrimSpace(tx.Type)),
		Platform:    strings.ToLower(strings.TrimSpace(tx.Source)),
		Programs:    programs,
		Description: tx.Description,
		ValueSol:    float64(netLamports) / lamportsPerSOL,
		FeeLamports: tx.Fee,
		FeeSol:      float64(tx.Fee) / lamportsPerSOL,
	}
}

func programNameFromID(programID string) string {
	switch strings.TrimSpace(programID) {
	case "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4":
		return "jupiter"
	case "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8":
		return "raydium"
	case "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc":
		return "orca"
	case "LBUZKhRxPF3XUpBCjp4YzTKgLccF8UDM5B2YfpT91fM":
		return "meteora"
	case "MarBmsSgKXdrN1egZf5sqe1TMai9K1rChYNDJgjq7aD":
		return "marinade"
	case "Jito4APyf642JPZPx3hGc6WWJ8zPKtRbRs4P815Awbb":
		return "jito"
	case "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":
		return "pumpfun"
	default:
		return ""
	}
}

func inferPrograms(tx heliusTx) []string {
	seen := make(map[string]struct{})
	out := make([]string, 0, 4)
	addProgram := func(name string) {
		name = strings.ToLower(strings.TrimSpace(name))
		if name == "" {
			return
		}
		if _, ok := seen[name]; ok {
			return
		}
		seen[name] = struct{}{}
		out = append(out, name)
	}

	addProgram(tx.Source)

	for _, instruction := range tx.Instructions {
		addProgram(programNameFromID(instruction.ProgramID))
	}
	for _, group := range tx.InnerInstructions {
		for _, instruction := range group.Instructions {
			addProgram(programNameFromID(instruction.ProgramID))
		}
	}

	if len(out) == 0 {
		addProgram("system")
	}
	return out
}

func shouldHideSpamTx(item accountTxItem) bool {
	desc := strings.ToLower(item.Description)
	if strings.Contains(desc, "spam") {
		return true
	}
	if item.Type == "nft_airdrop" || item.Type == "compressed_nft_airdrop" {
		return true
	}
	return false
}

// GetAccountTransactions proxies account transactions via Helius and returns
// a normalized Solscan-style payload with optional server-side filtering.
func (m *MarketProxy) GetAccountTransactions(w http.ResponseWriter, r *http.Request) {
	wallet := strings.TrimSpace(chi.URLParam(r, "wallet"))
	if wallet == "" {
		writeError(w, http.StatusBadRequest, "wallet parameter required")
		return
	}
	if !isValidSolanaAddress(wallet) {
		writeError(w, http.StatusBadRequest, "invalid wallet address")
		return
	}

	if m.heliusAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "helius API key not configured")
		return
	}

	limit := parseIntParam(r.URL.Query().Get("limit"), 50, 1, 100)
	before := strings.TrimSpace(r.URL.Query().Get("before"))
	sortOrder := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("sort")))
	if sortOrder == "" {
		sortOrder = "desc"
	}

	allowedTypes := splitCSVLower(r.URL.Query().Get("type"))
	allowedProtocols := splitCSVLower(r.URL.Query().Get("protocol"))
	hideFailed := parseBoolParam(r.URL.Query().Get("hideFailed"), false)
	hideSpam := parseBoolParam(r.URL.Query().Get("hideSpam"), true)
	cutoff := timeRangeCutoff(r.URL.Query().Get("timeRange"), time.Now())

	cacheKey := fmt.Sprintf(
		"account-tx:%s:%d:%s:%s:%s:%s:%t:%t:%d",
		wallet,
		limit,
		before,
		r.URL.Query().Get("type"),
		r.URL.Query().Get("protocol"),
		sortOrder,
		hideFailed,
		hideSpam,
		cutoff,
	)
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	values := url.Values{}
	values.Set("limit", strconv.Itoa(limit))
	if before != "" {
		values.Set("before", before)
	}

	apiURL := fmt.Sprintf(
		"https://api.helius.xyz/v0/addresses/%s/transactions?%s",
		wallet,
		values.Encode(),
	)

	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	req.Header.Set("Authorization", "Bearer "+m.heliusAPIKey)

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("helius account transactions error", "error", err, "wallet", wallet)
		writeError(w, http.StatusBadGateway, "account transactions unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read account transactions response")
		return
	}

	if resp.StatusCode != http.StatusOK {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(resp.StatusCode)
		w.Write(body)
		return
	}

	var heliusTxs []heliusTx
	if err := json.Unmarshal(body, &heliusTxs); err != nil {
		slog.Error("failed to decode helius account transactions", "error", err)
		writeError(w, http.StatusBadGateway, "invalid account transaction payload")
		return
	}

	walletLower := strings.ToLower(wallet)
	items := make([]accountTxItem, 0, len(heliusTxs))
	for _, tx := range heliusTxs {
		item := normalizeHeliusTx(tx, walletLower)
		if hideFailed && !item.Success {
			continue
		}
		if hideSpam && shouldHideSpamTx(item) {
			continue
		}
		if cutoff > 0 && item.BlockTime > 0 && item.BlockTime < cutoff {
			continue
		}
		if len(allowedTypes) > 0 {
			if _, ok := allowedTypes[item.Type]; !ok {
				continue
			}
		}
		if len(allowedProtocols) > 0 {
			if _, ok := allowedProtocols[item.Platform]; !ok {
				continue
			}
		}
		items = append(items, item)
	}

	if sortOrder == "asc" {
		for i, j := 0, len(items)-1; i < j; i, j = i+1, j-1 {
			items[i], items[j] = items[j], items[i]
		}
	}

	nextBefore := ""
	if len(items) > 0 {
		nextBefore = items[len(items)-1].Signature
	}

	response := map[string]any{
		"wallet":       wallet,
		"count":        len(items),
		"nextBefore":   nextBefore,
		"transactions": items,
	}
	normalizedBody, err := json.Marshal(response)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to serialize transactions")
		return
	}

	m.cache.Set(cacheKey, normalizedBody, accountTxCacheTTL)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(http.StatusOK)
	w.Write(normalizedBody)
}

// GetJitoTipFloor proxies GET /market/jito/tip-floor to Jito bundles API.
// Avoids CORS issues when called directly from the browser.
func (m *MarketProxy) GetJitoTipFloor(w http.ResponseWriter, r *http.Request) {
	const cacheKey = "jito:tip-floor"
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet,
		"https://bundles.jito.wtf/api/v1/bundles/tip_floor", nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("jito tip floor error", "error", err)
		writeError(w, http.StatusBadGateway, "jito tip floor unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read jito tip floor response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, 30*time.Second)
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetJitoTipAccounts proxies GET /market/jito/tip-accounts to the Jito Block
// Engine's `getTipAccounts` JSON-RPC method. The Block Engine rotates this list
// occasionally; previously the frontend kept a hardcoded copy that would
// silently go stale. Cached for 1 hour — these addresses don't change often.
func (m *MarketProxy) GetJitoTipAccounts(w http.ResponseWriter, r *http.Request) {
	const cacheKey = "jito:tip-accounts"
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	rpcBody := []byte(`{"jsonrpc":"2.0","id":1,"method":"getTipAccounts","params":[]}`)
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost,
		"https://mainnet.block-engine.jito.wtf/api/v1/bundles", bytes.NewReader(rpcBody))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("jito tip accounts error", "error", err)
		writeError(w, http.StatusBadGateway, "jito tip accounts unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read jito tip accounts response")
		return
	}

	if resp.StatusCode == http.StatusOK {
		// Reshape `{result: [...]}` to `{accounts: [...]}` so callers don't have to
		// know about JSON-RPC wrapping. Cache the reshaped form.
		var rpc struct {
			Result []string `json:"result"`
		}
		if err := json.Unmarshal(body, &rpc); err == nil && len(rpc.Result) > 0 {
			reshaped, _ := json.Marshal(map[string][]string{"accounts": rpc.Result})
			m.cache.Set(cacheKey, reshaped, 60*time.Minute)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			w.Write(reshaped)
			return
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetJitoBundleStatus proxies GET /market/jito/bundle/{bundleId} to Jito Block Engine.
func (m *MarketProxy) GetJitoBundleStatus(w http.ResponseWriter, r *http.Request) {
	bundleId := chi.URLParam(r, "bundleId")
	if bundleId == "" {
		writeError(w, http.StatusBadRequest, "bundleId parameter required")
		return
	}

	apiURL := fmt.Sprintf("https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles/%s", bundleId)
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("jito bundle status error", "error", err)
		writeError(w, http.StatusBadGateway, "jito bundle status unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read bundle status response")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// ─────────────────────────────────────────────────────────────────────────────
// Kobe Analytics API — kobe.mainnet.jito.network
// All kobe endpoints are public and require no authentication.
// ─────────────────────────────────────────────────────────────────────────────

const jitoKobeAPI = "https://kobe.mainnet.jito.network"

// forwardKobe proxies a GET or POST request to the Jito kobe analytics API.
// For POST requests, the entire request body is forwarded verbatim.
// If cacheKey is non-empty and the response is HTTP 200, the response is cached
// for cacheTTL. Use an empty cacheKey to disable caching (e.g. user-specific queries).
func (m *MarketProxy) forwardKobe(w http.ResponseWriter, r *http.Request, kobePath, cacheKey string, cacheTTL time.Duration) {
	if cacheKey != "" {
		if data, ok := m.cache.Get(cacheKey); ok {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("X-Cache", "HIT")
			w.Write(data)
			return
		}
	}

	var reqBody io.Reader
	if r.Method == http.MethodPost {
		body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		if err != nil {
			writeError(w, http.StatusBadRequest, "failed to read request body")
			return
		}
		reqBody = bytes.NewReader(body)
	}

	req, err := http.NewRequestWithContext(r.Context(), r.Method, jitoKobeAPI+kobePath, reqBody)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create request")
		return
	}
	if r.Method == http.MethodPost {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("kobe API error", "path", kobePath, "error", err)
		writeError(w, http.StatusBadGateway, "kobe API unavailable")
		return
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read kobe response")
		return
	}

	if cacheKey != "" && resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, respBody, cacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	w.Write(respBody)
}

// GetJitoStakePoolStats proxies POST /market/jito/stake-pool-stats to kobe API.
// Returns TVL, APY, supply, and validator count time series.
func (m *MarketProxy) GetJitoStakePoolStats(w http.ResponseWriter, r *http.Request) {
	m.forwardKobe(w, r, "/api/v1/stake_pool_stats", "", 0)
}

// GetJitosolSolRatio proxies POST /market/jito/jitosol-sol-ratio to kobe API.
// Returns jitoSOL/SOL exchange rate history.
func (m *MarketProxy) GetJitosolSolRatio(w http.ResponseWriter, r *http.Request) {
	m.forwardKobe(w, r, "/api/v1/jitosol_sol_ratio", "", 0)
}

// PostJitoStakerRewards proxies POST /market/jito/staker-rewards to kobe API.
// Not cached: responses depend on request body parameters (wallet, epoch, etc.).
func (m *MarketProxy) PostJitoStakerRewards(w http.ResponseWriter, r *http.Request) {
	m.forwardKobe(w, r, "/api/v1/staker_rewards", "", 0)
}

// PostJitoValidatorRewards proxies POST /market/jito/validator-rewards to kobe API.
func (m *MarketProxy) PostJitoValidatorRewards(w http.ResponseWriter, r *http.Request) {
	m.forwardKobe(w, r, "/api/v1/validator_rewards", "", 0)
}

// PostJitoValidators proxies POST /market/jito/validators to kobe API.
// Returns all Solana validators with MEV metrics.
func (m *MarketProxy) PostJitoValidators(w http.ResponseWriter, r *http.Request) {
	m.forwardKobe(w, r, "/api/v1/validators", "", 0)
}

// PostJitosolValidators proxies POST /market/jito/jitosol-validators to kobe API.
// Returns validators in the JitoSOL stake pool.
func (m *MarketProxy) PostJitosolValidators(w http.ResponseWriter, r *http.Request) {
	m.forwardKobe(w, r, "/api/v1/jitosol_validators", "", 0)
}

// GetJitoValidatorHistory proxies GET /market/jito/validators/{voteAccount} to kobe API.
// Returns historical MEV reward data for a single validator.
func (m *MarketProxy) GetJitoValidatorHistory(w http.ResponseWriter, r *http.Request) {
	voteAccount := chi.URLParam(r, "voteAccount")
	if !isValidSolanaAddress(voteAccount) {
		writeError(w, http.StatusBadRequest, "invalid vote account address")
		return
	}
	cacheKey := "jito:validator-history:" + voteAccount
	m.forwardKobe(w, r, "/api/v1/validators/"+voteAccount, cacheKey, 60*time.Second)
}

// GetJitoMevRewards proxies GET or POST /market/jito/mev-rewards to kobe API.
// GET returns latest epoch; POST body may contain { "epoch": N }.
func (m *MarketProxy) GetJitoMevRewards(w http.ResponseWriter, r *http.Request) {
	m.forwardKobe(w, r, "/api/v1/mev_rewards", "", 0)
}

// GetJitoDailyMevRewards proxies GET /market/jito/daily-mev-rewards to kobe API.
// Returns MEV tips aggregated by calendar day.
func (m *MarketProxy) GetJitoDailyMevRewards(w http.ResponseWriter, r *http.Request) {
	const cacheKey = "jito:daily-mev-rewards"
	m.forwardKobe(w, r, "/api/v1/daily_mev_rewards", cacheKey, 5*time.Minute)
}

// GetJitoStakeOverTime proxies GET /market/jito/stake-over-time to kobe API.
// Returns fraction of Solana stake on Jito validators per epoch.
func (m *MarketProxy) GetJitoStakeOverTime(w http.ResponseWriter, r *http.Request) {
	const cacheKey = "jito:stake-over-time"
	m.forwardKobe(w, r, "/api/v1/jito_stake_over_time", cacheKey, 5*time.Minute)
}

// GetJitoPreferredWithdrawValidators proxies GET /market/jito/preferred-withdraw-validators
// to kobe API. Supports query params: limit, min_stake_threshold, randomized.
func (m *MarketProxy) GetJitoPreferredWithdrawValidators(w http.ResponseWriter, r *http.Request) {
	queryStr := r.URL.RawQuery
	path := "/api/v1/preferred_withdraw_validator_list"
	if queryStr != "" {
		path += "?" + queryStr
	}
	cacheKey := "jito:preferred-validators:" + queryStr
	m.forwardKobe(w, r, path, cacheKey, 30*time.Second)
}

// GetJitoMevCommissionAverageOverTime proxies GET /market/jito/mev-commission-avg to kobe API.
// Returns stake-weighted average MEV commission rates by epoch.
func (m *MarketProxy) GetJitoMevCommissionAverageOverTime(w http.ResponseWriter, r *http.Request) {
	const cacheKey = "jito:mev-commission-avg"
	m.forwardKobe(w, r, "/api/v1/mev_commission_average_over_time", cacheKey, 5*time.Minute)
}

// PostJitoStewardEvents proxies POST /market/jito/steward-events to kobe API.
// Supports filtering by event_type, vote_account, epoch, limit, skip.
func (m *MarketProxy) PostJitoStewardEvents(w http.ResponseWriter, r *http.Request) {
	m.forwardKobe(w, r, "/api/v1/steward_events", "", 0)
}

// GetJitoBamEpochMetrics proxies GET /market/jito/bam-epoch-metrics?epoch=N to kobe API.
func (m *MarketProxy) GetJitoBamEpochMetrics(w http.ResponseWriter, r *http.Request) {
	epoch := r.URL.Query().Get("epoch")
	path := "/api/v1/bam_epoch_metrics"
	if epoch != "" {
		if _, err := strconv.ParseUint(epoch, 10, 64); err != nil {
			writeError(w, http.StatusBadRequest, "invalid epoch parameter")
			return
		}
		path += "?epoch=" + epoch
	}
	cacheKey := "jito:bam-epoch-metrics:" + epoch
	m.forwardKobe(w, r, path, cacheKey, 60*time.Second)
}

// GetJitoBamValidators proxies GET /market/jito/bam-validators?epoch=N to kobe API.
func (m *MarketProxy) GetJitoBamValidators(w http.ResponseWriter, r *http.Request) {
	epoch := r.URL.Query().Get("epoch")
	path := "/api/v1/bam_validators"
	if epoch != "" {
		if _, err := strconv.ParseUint(epoch, 10, 64); err != nil {
			writeError(w, http.StatusBadRequest, "invalid epoch parameter")
			return
		}
		path += "?epoch=" + epoch
	}
	cacheKey := "jito:bam-validators:" + epoch
	m.forwardKobe(w, r, path, cacheKey, 60*time.Second)
}

// GetJitoBamDelegationBlacklist proxies GET /market/jito/bam-delegation-blacklist to kobe API.
func (m *MarketProxy) GetJitoBamDelegationBlacklist(w http.ResponseWriter, r *http.Request) {
	const cacheKey = "jito:bam-blacklist"
	m.forwardKobe(w, r, "/api/v1/bam_delegation_blacklist", cacheKey, 5*time.Minute)
}

// GetJitoBamValidatorScore proxies GET /market/jito/bam-validator-score?epoch=N&vote_account=X
// to kobe API. Returns scoring components for a specific validator.
func (m *MarketProxy) GetJitoBamValidatorScore(w http.ResponseWriter, r *http.Request) {
	epoch := r.URL.Query().Get("epoch")
	voteAccount := r.URL.Query().Get("vote_account")
	if epoch != "" {
		if _, err := strconv.ParseUint(epoch, 10, 64); err != nil {
			writeError(w, http.StatusBadRequest, "invalid epoch parameter")
			return
		}
	}
	if voteAccount != "" && !isValidSolanaAddress(voteAccount) {
		writeError(w, http.StatusBadRequest, "invalid vote_account parameter")
		return
	}
	queryStr := r.URL.RawQuery
	path := "/api/v1/bam_validator_score"
	if queryStr != "" {
		path += "?" + queryStr
	}
	cacheKey := "jito:bam-score:" + epoch + ":" + voteAccount
	m.forwardKobe(w, r, path, cacheKey, 60*time.Second)
}

// GetMarinadeExchangeRate fetches the live mSOL/SOL spot rate from Marinade's
// own indexer.
//
// Primary: https://api.marinade.finance/msol/price_sol — returns the rate as
// a bare numeric body (e.g. "1.377545615248593"), recomputed by Marinade's
// indexer on every Solana slot. This is the live spot price their staking
// dashboard uses, NOT an aggregated APY rollup.
//
// Fallback: https://api.marinade.finance/tlv — derives the rate from
// `total_virtual_staked_sol / msol_supply` (here `msol_directed_stake_msol`
// is the wrong field; we instead reach for total_sol and assume the indexer
// keeps these in sync). Used only if the price endpoint fails.
//
// Response (always wrapped so the client schema stays stable):
//
//	{ "msolPrice": 1.377545, "source": "price_sol" | "tlv" }
//
// On total upstream failure we return 502 — callers must hide UI rather than
// fall back to a static constant.
func (m *MarketProxy) GetMarinadeExchangeRate(w http.ResponseWriter, r *http.Request) {
	const cacheKey = "marinade:exchange-rate"
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	rate, source, err := m.fetchMarinadeRate(r.Context())
	if err != nil {
		slog.Error("marinade exchange rate error", "error", err)
		writeError(w, http.StatusBadGateway, "marinade rate unavailable")
		return
	}

	out, _ := json.Marshal(map[string]any{
		"msolPrice": rate,
		"source":    source,
	})
	// Cache for 15s — long enough to absorb traffic spikes, short enough to
	// stay effectively live (Marinade's indexer updates ~per slot ≈ 400ms).
	m.cache.Set(cacheKey, out, 15*time.Second)

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(http.StatusOK)
	w.Write(out)
}

// GetJupSolExchangeRate returns the live SOL→jupSOL conversion rate the user
// will actually receive when they stake.
//
// JupSOL stake is implemented as a Jupiter swap (SOL → jupSOL), so the
// "expected receive" preview must come from the same swap router that will
// build the transaction — not from a stake-pool redemption rate, which would
// drift from the actual on-chain output by the AMM spread. We hit the public
// quote endpoint with a 1 SOL probe (1e9 lamports, 10 bps slippage, indirect
// routing allowed) and expose the result as `solPerJupSol = 1e9 / outAmount`.
//
// Response (wrapped so the client schema stays stable across rate sources):
//
//	{ "jupSolPrice": 1.0876, "source": "jupiter_quote" }
//
// On upstream failure we return 502 — callers must hide the preview rather
// than fall back to a static constant.
func (m *MarketProxy) GetJupSolExchangeRate(w http.ResponseWriter, r *http.Request) {
	const cacheKey = "jupsol:exchange-rate"
	if data, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Write(data)
		return
	}

	rate, err := m.fetchJupSolRate(r.Context())
	if err != nil {
		slog.Error("jupsol exchange rate error", "error", err)
		writeError(w, http.StatusBadGateway, "jupsol rate unavailable")
		return
	}

	out, _ := json.Marshal(map[string]any{
		"jupSolPrice": rate,
		"source":      "jupiter_quote",
	})
	// 30s — JupSOL accrues yield slowly; a half-minute snapshot stays well
	// inside the user's perceived freshness window without hammering Jupiter.
	m.cache.Set(cacheKey, out, 30*time.Second)

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Cache", "MISS")
	w.WriteHeader(http.StatusOK)
	w.Write(out)
}

// fetchJupSolRate probes the Jupiter swap quote endpoint for 1 SOL → jupSOL
// and returns the inverse outAmount as SOL-per-jupSOL. Goes through the keyed
// `api.jup.ag` host when a Jupiter API key is configured (higher rate limits,
// fresher quotes); falls back to public `lite-api.jup.ag` otherwise.
func (m *MarketProxy) fetchJupSolRate(ctx context.Context) (float64, error) {
	const (
		solMint    = "So11111111111111111111111111111111111111112"
		jupSolMint = "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v"
	)
	q := url.Values{}
	q.Set("inputMint", solMint)
	q.Set("outputMint", jupSolMint)
	q.Set("amount", "1000000000") // 1 SOL in lamports
	q.Set("slippageBps", "10")
	q.Set("onlyDirectRoutes", "false")
	endpoint := m.jupiterHost() + "/swap/v1/quote?" + q.Encode()

	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	m.applyJupiterAuth(req)
	resp, err := m.client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 0, fmt.Errorf("jupiter quote HTTP %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	if err != nil {
		return 0, err
	}
	var parsed struct {
		OutAmount string `json:"outAmount"`
	}
	if err := json.Unmarshal(body, &parsed); err != nil {
		return 0, fmt.Errorf("jupiter quote parse: %w", err)
	}
	out, err := strconv.ParseUint(parsed.OutAmount, 10, 64)
	if err != nil || out == 0 {
		return 0, fmt.Errorf("jupiter quote outAmount invalid: %q", parsed.OutAmount)
	}
	// SOL-per-jupSOL = (1 SOL in lamports) / (jupSOL out in lamports). Both
	// mints have 9 decimals so the lamport ratio equals the unit ratio.
	rate := 1e9 / float64(out)
	if !(rate > 0) {
		return 0, fmt.Errorf("jupiter quote rate non-positive: %v", rate)
	}
	return rate, nil
}

// fetchMarinadeRate hits Marinade's dedicated live-price endpoint. There is
// no static or derived fallback: if the endpoint is down we surface the error
// so the UI can refuse to show a stale or guessed conversion. (`/tlv` does
// not carry total mSOL supply, only directed-stake numerators, so deriving
// the rate from it would be wrong by construction.)
func (m *MarketProxy) fetchMarinadeRate(ctx context.Context) (float64, string, error) {
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, "https://api.marinade.finance/msol/price_sol", nil)
	resp, err := m.client.Do(req)
	if err != nil {
		return 0, "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 0, "", fmt.Errorf("marinade price_sol HTTP %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64))
	if err != nil {
		return 0, "", err
	}
	v, err := strconv.ParseFloat(strings.TrimSpace(string(body)), 64)
	if err != nil {
		return 0, "", fmt.Errorf("marinade price_sol parse: %w", err)
	}
	if !(v > 0) {
		return 0, "", fmt.Errorf("marinade price_sol non-positive: %v", v)
	}
	return v, "price_sol", nil
}

// rpcEndpoint represents one entry in the fallback chain.
type rpcEndpoint struct {
	url       string
	authToken string // optional Bearer token for managed endpoints
	label     string // for logging
}

// rpcChain returns the ordered list of RPC endpoints to try. Helius first
// (best performance + ratelimit headroom), then any operator-configured
// fallbacks via OPRAI_RPC_FALLBACKS (comma-separated URLs), finally the
// public mainnet-beta endpoint as a last resort.
func (m *MarketProxy) rpcChain() []rpcEndpoint {
	chain := make([]rpcEndpoint, 0, 3)
	if m.heliusAPIKey != "" {
		chain = append(chain, rpcEndpoint{
			url:       "https://mainnet.helius-rpc.com",
			authToken: m.heliusAPIKey,
			label:     "helius",
		})
	}
	if extra := os.Getenv("OPRAI_RPC_FALLBACKS"); extra != "" {
		for _, raw := range strings.Split(extra, ",") {
			url := strings.TrimSpace(raw)
			if url == "" {
				continue
			}
			chain = append(chain, rpcEndpoint{url: url, label: url})
		}
	}
	chain = append(chain, rpcEndpoint{
		url:   "https://api.mainnet-beta.solana.com",
		label: "mainnet-beta",
	})
	return chain
}

// PostRpc proxies Solana JSON-RPC POST requests with a fallback chain so a
// single-provider outage doesn't take down the frontend's RPC needs. Tries
// each endpoint in order until one returns a 2xx; only 5xx / network errors
// trigger fallthrough (4xx is treated as a client error and surfaced).
func (m *MarketProxy) PostRpc(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20)) // 1 MB limit
	if err != nil {
		writeError(w, http.StatusBadRequest, "failed to read request body")
		return
	}

	chain := m.rpcChain()
	var lastStatus int
	var lastBody []byte

	for i, ep := range chain {
		req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, ep.url, strings.NewReader(string(body)))
		if err != nil {
			continue
		}
		req.Header.Set("Content-Type", "application/json")
		if ep.authToken != "" {
			req.Header.Set("Authorization", "Bearer "+ep.authToken)
		}

		resp, err := m.client.Do(req)
		if err != nil {
			slog.Warn("RPC proxy: endpoint failed (network)", "endpoint", ep.label, "attempt", i+1, "error", err)
			continue
		}
		respBody, readErr := io.ReadAll(resp.Body)
		resp.Body.Close()
		if readErr != nil {
			slog.Warn("RPC proxy: endpoint read failed", "endpoint", ep.label, "error", readErr)
			continue
		}

		// 5xx → try next endpoint. 4xx and 2xx → return to client.
		if resp.StatusCode >= 500 {
			slog.Warn("RPC proxy: endpoint 5xx", "endpoint", ep.label, "status", resp.StatusCode)
			lastStatus = resp.StatusCode
			lastBody = respBody
			continue
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(resp.StatusCode)
		w.Write(respBody)
		return
	}

	// Exhausted the chain — surface the last 5xx if we have one, else a generic 502.
	if lastStatus >= 500 && lastBody != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(lastStatus)
		w.Write(lastBody)
		return
	}
	writeError(w, http.StatusBadGateway, "all RPC endpoints unavailable")
}

// PostHeliusTransactions proxies POST /market/helius/transactions to Helius Enhanced Transactions API.
// Accepts: { "transactions": ["sig1", "sig2", ...] }
func (m *MarketProxy) PostHeliusTransactions(w http.ResponseWriter, r *http.Request) {
	if m.heliusAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "Helius service not configured")
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		writeError(w, http.StatusBadRequest, "failed to read request body")
		return
	}

	apiURL := "https://api.helius.xyz/v0/transactions"
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, apiURL, strings.NewReader(string(body)))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create Helius request")
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+m.heliusAPIKey)

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("helius transactions proxy error", "error", err)
		writeError(w, http.StatusBadGateway, "Helius service unavailable")
		return
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read Helius response")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	w.Write(respBody)
}

// ─────────────────────────────────────────────────────────────────────────────
// Jupiter Portfolio API — api.jup.ag/portfolio/v1
// Returns the wallet's positions across Jupiter's own products only (DCA,
// limit orders, perpetuals, lend, JUP/JupSOL stake, Jupiter LP). Cross-protocol
// aggregation (Kamino, Meteora, Orca, Pumpfun) is NOT covered here — those go
// through their own dedicated proxies / on-chain reads.
//
// All endpoints require the x-api-key header (paid tier on api.jup.ag); the
// lite host (lite-api.jup.ag) returns 404 for /portfolio/* as of 2026-01-31.
// We always hit api.jup.ag and inject the key server-side so the browser
// never sees it.
// ─────────────────────────────────────────────────────────────────────────────

// forwardJupiterPortfolio runs a GET against api.jup.ag/portfolio/v1{path},
// forwards rate-limit headers to the caller so the frontend can back off, and
// caches the response under cacheKey for cacheTTL when the upstream is healthy.
// Passes through optional `platforms` query param if present on the inbound
// request.
func (m *MarketProxy) forwardJupiterPortfolio(w http.ResponseWriter, r *http.Request, path, cacheKey string, cacheTTL time.Duration) {
	if cacheKey != "" {
		if data, ok := m.cache.Get(cacheKey); ok {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("X-Cache", "HIT")
			w.Write(data)
			return
		}
	}

	apiURL := "https://api.jup.ag/portfolio/v1" + path
	if q := r.URL.Query().Get("platforms"); q != "" {
		apiURL += "?platforms=" + url.QueryEscape(q)
	}

	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, apiURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create jupiter portfolio request")
		return
	}
	m.applyJupiterAuth(req)

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("jupiter portfolio error", "path", path, "error", err)
		writeError(w, http.StatusBadGateway, "jupiter portfolio unavailable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to read jupiter portfolio response")
		return
	}

	// Forward rate-limit signal so the frontend can throttle. x-ratelimit-* is
	// what the Jupiter docs emit on the paid plan; we surface it verbatim.
	for _, h := range []string{"x-ratelimit-remaining", "x-ratelimit-reset", "x-ratelimit-current"} {
		if v := resp.Header.Get(h); v != "" {
			w.Header().Set(h, v)
		}
	}

	if cacheKey != "" && resp.StatusCode == http.StatusOK {
		m.cache.Set(cacheKey, body, cacheTTL)
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// GetJupiterPortfolioPositions proxies
// GET /market/jupiter/portfolio/positions/{wallet}[?platforms=…]
// → GET https://api.jup.ag/portfolio/v1/positions/{wallet}
// Returns Jupiter products positions only (DCA, limit, perp, lend, JUP/JupSOL
// stake, LP). 30s cache — short because positions move with on-chain activity.
func (m *MarketProxy) GetJupiterPortfolioPositions(w http.ResponseWriter, r *http.Request) {
	wallet := chi.URLParam(r, "wallet")
	if !isValidSolanaAddress(wallet) {
		writeError(w, http.StatusBadRequest, "invalid wallet address")
		return
	}
	cacheKey := "jup:portfolio:positions:" + wallet + ":" + r.URL.Query().Get("platforms")
	m.forwardJupiterPortfolio(w, r, "/positions/"+wallet, cacheKey, 30*time.Second)
}

// GetJupiterStakedJup proxies
// GET /market/jupiter/portfolio/staked-jup/{wallet}
// → GET https://api.jup.ag/portfolio/v1/staked-jup/{wallet}
// Returns active JUP vote-escrow / staking info: {stakedAmount, unstaking[]}.
func (m *MarketProxy) GetJupiterStakedJup(w http.ResponseWriter, r *http.Request) {
	wallet := chi.URLParam(r, "wallet")
	if !isValidSolanaAddress(wallet) {
		writeError(w, http.StatusBadRequest, "invalid wallet address")
		return
	}
	cacheKey := "jup:portfolio:staked-jup:" + wallet
	m.forwardJupiterPortfolio(w, r, "/staked-jup/"+wallet, cacheKey, 60*time.Second)
}

// GetJupiterPortfolioPlatforms proxies
// GET /market/jupiter/portfolio/platforms → /portfolio/v1/platforms
// Returns the full platforms catalog (for label / logo lookup). 1h cache —
// this list rarely changes.
func (m *MarketProxy) GetJupiterPortfolioPlatforms(w http.ResponseWriter, r *http.Request) {
	m.forwardJupiterPortfolio(w, r, "/platforms", "jup:portfolio:platforms", 60*time.Minute)
}

// GetPumpfunCreatorRewards proxies
// GET /market/pumpfun/rewards/{wallet}
//
// STUB (PR 1): pump.fun does not expose a public creator-rewards HTTP endpoint.
// frontend-api.pump.fun is offline (CF 1016); frontend-api-v3.pump.fun has no
// /creator-rewards path (all variants probed → 404). Real implementation
// requires on-chain decode of the pump.fun `creator-vault` PDA:
//
//	seeds   = ["creator-vault", creator_pubkey]
//	program = 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P
//	read    = getMultipleAccounts → lamport balance → SOL/USD
//
// That lands in PR 3 alongside the rest of the portfolio analytics work.
// For now we return an empty contract so the frontend can wire the section
// without conditional rendering churn.
func (m *MarketProxy) GetPumpfunCreatorRewards(w http.ResponseWriter, r *http.Request) {
	wallet := chi.URLParam(r, "wallet")
	if !isValidSolanaAddress(wallet) {
		writeError(w, http.StatusBadRequest, "invalid wallet address")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write([]byte(`{"wallet":"` + wallet + `","claimableLamports":0,"claimableSol":0,"claimableUsd":0,"rewards":[]}`))
}

type tokenMetaResult struct {
	Name   string `json:"name"`
	Symbol string `json:"symbol"`
	Image  string `json:"image"`
}

// PostTokenMeta resolves on-chain token metadata (name / symbol / logo) for a
// batch of mints via Helius getAsset — SERVER-SIDE. Client-side per-mint
// resolution was unreliable: the browser's ~6-connection-per-host cap made the
// getAsset calls queue behind the portfolio's other reads and time out, and the
// alternate source (jup.ag) was intermittently unresolvable. Doing it here
// (parallel goroutines, no browser cap, cached 30m) is fast and dependable.
// POST body: {"mints":[...]}. Response: {mint: {name, symbol, image}}.
func (m *MarketProxy) PostTokenMeta(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<16))
	if err != nil {
		writeError(w, http.StatusBadRequest, "failed to read body")
		return
	}
	var reqBody struct {
		Mints []string `json:"mints"`
	}
	if err := json.Unmarshal(body, &reqBody); err != nil {
		writeError(w, http.StatusBadRequest, "invalid body")
		return
	}
	if m.heliusAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "helius not configured")
		return
	}
	if len(reqBody.Mints) > 100 {
		reqBody.Mints = reqBody.Mints[:100]
	}

	out := make(map[string]tokenMetaResult, len(reqBody.Mints))
	var mu sync.Mutex
	sem := make(chan struct{}, 10)
	var wg sync.WaitGroup
	for _, mint := range reqBody.Mints {
		if mint == "" {
			continue
		}
		if data, ok := m.cache.Get("tokenmeta:" + mint); ok {
			var tm tokenMetaResult
			if json.Unmarshal(data, &tm) == nil {
				mu.Lock()
				out[mint] = tm
				mu.Unlock()
				continue
			}
		}
		wg.Add(1)
		sem <- struct{}{}
		go func(mint string) {
			defer wg.Done()
			defer func() { <-sem }()
			tm, ok := m.resolveTokenMeta(r.Context(), mint)
			if !ok {
				return
			}
			if b, e := json.Marshal(tm); e == nil {
				m.cache.Set("tokenmeta:"+mint, b, tokenMetaCacheTTL)
			}
			mu.Lock()
			out[mint] = tm
			mu.Unlock()
		}(mint)
	}
	wg.Wait()

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

func (m *MarketProxy) resolveTokenMeta(ctx context.Context, mint string) (tokenMetaResult, bool) {
	payload, err := json.Marshal(map[string]any{
		"jsonrpc": "2.0",
		"id":      1,
		"method":  "getAsset",
		"params":  map[string]string{"id": mint},
	})
	if err != nil {
		return tokenMetaResult{}, false
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, "https://mainnet.helius-rpc.com", bytes.NewReader(payload))
	if err != nil {
		return tokenMetaResult{}, false
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+m.heliusAPIKey)
	resp, err := m.client.Do(req)
	if err != nil {
		return tokenMetaResult{}, false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return tokenMetaResult{}, false
	}
	var parsed struct {
		Result struct {
			Content struct {
				Metadata struct {
					Name   string `json:"name"`
					Symbol string `json:"symbol"`
				} `json:"metadata"`
				Links struct {
					Image string `json:"image"`
				} `json:"links"`
				Files []struct {
					URI string `json:"uri"`
				} `json:"files"`
			} `json:"content"`
		} `json:"result"`
	}
	if json.NewDecoder(resp.Body).Decode(&parsed) != nil {
		return tokenMetaResult{}, false
	}
	c := parsed.Result.Content
	image := c.Links.Image
	if image == "" {
		for _, f := range c.Files {
			if f.URI != "" {
				image = f.URI
				break
			}
		}
	}
	if c.Metadata.Name == "" && c.Metadata.Symbol == "" && image == "" {
		return tokenMetaResult{}, false
	}
	return tokenMetaResult{Name: c.Metadata.Name, Symbol: c.Metadata.Symbol, Image: image}, true
}
