package handlers

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log/slog"
	"math/big"
	"net/http"
	"sort"
	"strings"
	"time"

	"golang.org/x/crypto/sha3"
)

func keccak256(data []byte) []byte {
	h := sha3.NewLegacyKeccak256()
	h.Write(data)
	return h.Sum(nil)
}

func isHex(s string) bool {
	for _, c := range s {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) {
			return false
		}
	}
	return true
}

// EVM wallet portfolio via Alchemy's Data API (multichain balances + metadata +
// USD prices in ONE call). Birdeye can price EVM tokens but cannot enumerate an
// EVM wallet's holdings on our key, so Alchemy owns EVM reads end-to-end.

// evmNetwork maps an Alchemy network id to a human chain name + its native
// token's symbol/name. These five are the majors on the free tier.
type evmNetwork struct {
	id           string
	chain        string
	nativeSymbol string
	nativeName   string
}

var evmNetworks = []evmNetwork{
	{"eth-mainnet", "ethereum", "ETH", "Ethereum"},
	{"base-mainnet", "base", "ETH", "Ethereum"},
	{"arb-mainnet", "arbitrum", "ETH", "Ethereum"},
	{"opt-mainnet", "optimism", "ETH", "Ethereum"},
	{"matic-mainnet", "polygon", "POL", "Polygon"},
	{"bnb-mainnet", "bsc", "BNB", "BNB"},
}

// twChain maps our chain name to the Trust Wallet assets folder, used to build
// token logo URLs (Alchemy rarely returns logos). jsDelivr serves them reliably.
var twChain = map[string]string{
	"ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum",
	"optimism": "optimism", "polygon": "polygon", "bsc": "smartchain",
}

const twBase = "https://cdn.jsdelivr.net/gh/trustwallet/assets@master/blockchains/"

// tokenLogo returns a Trust Wallet CDN logo URL for a token (checksummed
// address) or its chain's NATIVE coin. Native ETH on an L2 (Base/Arbitrum/
// Optimism) shows the real ETH logo, not the chain's brand mark. The frontend
// falls back to DexScreener then a text badge if this 404s.
func tokenLogo(chain, tokenAddr string, native bool) string {
	if native {
		switch chain {
		case "ethereum", "base", "arbitrum", "optimism", "robinhood":
			return twBase + "ethereum/info/logo.png" // native coin is ETH
		case "polygon":
			return twBase + "polygon/info/logo.png" // POL
		case "bsc":
			return twBase + "smartchain/info/logo.png" // BNB
		default:
			return ""
		}
	}
	folder := twChain[chain]
	if folder == "" {
		return ""
	}
	base := twBase + folder
	cs := toChecksumAddress(tokenAddr)
	if cs == "" {
		return ""
	}
	return base + "/assets/" + cs + "/logo.png"
}

// toChecksumAddress applies EIP-55 mixed-case checksumming (Trust Wallet paths
// use checksummed addresses).
func toChecksumAddress(addr string) string {
	a := strings.ToLower(strings.TrimPrefix(strings.TrimSpace(addr), "0x"))
	if len(a) != 40 || !isHex(a) {
		return ""
	}
	hash := keccak256([]byte(a))
	hexHash := hex.EncodeToString(hash)
	out := make([]byte, 0, 42)
	out = append(out, '0', 'x')
	for i := 0; i < 40; i++ {
		c := a[i]
		if c >= 'a' && c <= 'f' && hexHash[i] >= '8' {
			c -= 32 // uppercase
		}
		out = append(out, c)
	}
	return string(out)
}

func networkMeta(id string) evmNetwork {
	for _, n := range evmNetworks {
		if n.id == id {
			return n
		}
	}
	return evmNetwork{id: id, chain: id, nativeSymbol: "", nativeName: ""}
}

// alchemyTokensResponse mirrors POST /data/v1/{key}/assets/tokens/by-address.
type alchemyTokensResponse struct {
	Data struct {
		Tokens []struct {
			Address       string `json:"address"`
			Network       string `json:"network"`
			TokenAddress  string `json:"tokenAddress"`
			TokenBalance  string `json:"tokenBalance"`
			TokenMetadata *struct {
				Name     string `json:"name"`
				Symbol   string `json:"symbol"`
				Decimals *int   `json:"decimals"`
				Logo     string `json:"logo"`
			} `json:"tokenMetadata"`
			TokenPrices []struct {
				Currency string `json:"currency"`
				Value    string `json:"value"`
			} `json:"tokenPrices"`
		} `json:"tokens"`
	} `json:"data"`
}

type evmToken struct {
	Chain    string  `json:"chain"`
	Network  string  `json:"network"`
	Address  string  `json:"address"` // token contract, or "native"
	Symbol   string  `json:"symbol"`
	Name     string  `json:"name"`
	Decimals int     `json:"decimals"`
	Logo     string  `json:"logo,omitempty"`
	UIAmount float64 `json:"uiAmount"`
	PriceUsd float64 `json:"priceUsd"`
	ValueUsd float64 `json:"valueUsd"`
	Native   bool    `json:"native"`
	Spam     bool    `json:"spam"`
}

// spamKeywords flag airdropped junk tokens whose name/symbol is a promo message
// (a URL, "claim", "airdrop", etc.) — the classic wallet-spam pattern.
var spamKeywords = []string{
	"http", "www.", ".com", ".club", ".supply", ".gift", ".xyz", ".io/", ".vv",
	".net", ".app", ".fi ", ".finance", "claim", "airdrop", "voucher", "reward",
	"access on", "visit", "t.me", "rewards", "bonus", "giveaway", "->",
}

// looksSpam: promo text in the name/symbol, or a priced-at-zero dust token that
// Alchemy couldn't value (native coins are never spam).
func looksSpam(symbol, name string, native bool, priceUsd float64) bool {
	if native {
		return false
	}
	s := strings.ToLower(symbol + " " + name)
	for _, kw := range spamKeywords {
		if strings.Contains(s, kw) {
			return true
		}
	}
	return priceUsd <= 0
}

// GetEvmPortfolio handles GET /portfolio/evm?address=0x.. — the linked EVM
// wallet's holdings across the major EVM chains, valued in USD.
func (m *MarketProxy) GetEvmPortfolio(w http.ResponseWriter, r *http.Request) {
	if m.alchemyAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "EVM portfolio is not configured")
		return
	}
	address := strings.TrimSpace(r.URL.Query().Get("address"))
	if !looksLikeEVMAddress(address) {
		writeError(w, http.StatusBadRequest, "A valid 0x EVM address is required")
		return
	}
	address = strings.ToLower(address)

	cacheKey := "evm-portfolio:" + address
	if cached, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write(cached)
		return
	}

	networkIDs := make([]string, 0, len(evmNetworks))
	for _, n := range evmNetworks {
		networkIDs = append(networkIDs, n.id)
	}
	reqBody, _ := json.Marshal(map[string]any{
		"addresses": []map[string]any{
			{"address": address, "networks": networkIDs},
		},
		"withMetadata":        true,
		"withPrices":          true,
		"includeNativeTokens": true,
	})

	url := fmt.Sprintf("https://api.g.alchemy.com/data/v1/%s/assets/tokens/by-address", m.alchemyAPIKey)
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, url, bytes.NewReader(reqBody))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Failed to build request")
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("alchemy evm portfolio error", "error", err)
		writeError(w, http.StatusBadGateway, "EVM portfolio provider unavailable")
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		slog.Error("alchemy evm portfolio non-200", "status", resp.StatusCode)
		writeError(w, http.StatusBadGateway, "EVM portfolio provider error")
		return
	}

	var parsed alchemyTokensResponse
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		slog.Error("alchemy evm portfolio decode", "error", err)
		writeError(w, http.StatusBadGateway, "EVM portfolio decode failed")
		return
	}

	out := normalizeEVMPortfolio(address, &parsed)

	// Robinhood Chain is not indexed by Alchemy — read it over JSON-RPC and merge
	// into the same multichain response. A miss returns nothing, never fails.
	// Reuse the live mainnet ETH price for Robinhood's (bridged, same) ETH.
	ethPrice := 0.0
	if toks, ok := out["tokens"].([]evmToken); ok {
		for _, t := range toks {
			// Only borrow the price from an ETH-native holding (eth/base/arb/opt).
			// Native tokens ALSO include POL (Polygon) and BNB (BSC); picking the
			// first native-with-price indiscriminately set Robinhood ETH to POL's
			// ~$0.09 when the wallet held a little native POL/BNB.
			if t.Native && t.PriceUsd > 0 && strings.EqualFold(t.Symbol, "ETH") {
				ethPrice = t.PriceUsd
				break
			}
		}
	}
	// No mainnet ETH holding to borrow from (a Robinhood-only wallet, or one that
	// only holds POL/BNB natively) — fetch a live ETH/USD directly, else the
	// wallet's Robinhood ETH + WETH would show unpriced ($0) or mispriced.
	if ethPrice <= 0 {
		ethPrice = m.ethPriceUSD(r)
	}
	if rhTokens, rhUsd := m.fetchRobinhoodTokens(r, address, ethPrice); len(rhTokens) > 0 {
		if toks, ok := out["tokens"].([]evmToken); ok {
			toks = append(toks, rhTokens...)
			sort.SliceStable(toks, func(i, j int) bool { return toks[i].ValueUsd > toks[j].ValueUsd })
			out["tokens"] = toks
		}
		if tu, ok := out["totalUsd"].(float64); ok {
			out["totalUsd"] = tu + rhUsd
		}
	}

	body, err := json.Marshal(out)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Failed to encode portfolio")
		return
	}
	m.cache.Set(cacheKey, body, 45*time.Second)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(body)
}

// ethPriceUSD returns a live ETH/USD price, cached 60s. Used to price Robinhood
// Chain ETH/WETH when the Alchemy multichain response carried no mainnet ETH to
// borrow a price from (a Robinhood-only wallet).
func (m *MarketProxy) ethPriceUSD(r *http.Request) float64 {
	const ck = "eth-price-usd"
	if cached, ok := m.cache.Get(ck); ok {
		return parseFloat(string(cached))
	}
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet,
		"https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd", nil)
	if err != nil {
		return 0
	}
	resp, err := m.client.Do(req)
	if err != nil {
		slog.Warn("eth price fetch error", "error", err)
		return 0
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 0
	}
	var parsed struct {
		Ethereum struct {
			USD float64 `json:"usd"`
		} `json:"ethereum"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil || parsed.Ethereum.USD <= 0 {
		return 0
	}
	m.cache.Set(ck, []byte(fmt.Sprintf("%g", parsed.Ethereum.USD)), 60*time.Second)
	return parsed.Ethereum.USD
}

func normalizeEVMPortfolio(address string, parsed *alchemyTokensResponse) map[string]any {
	tokens := make([]evmToken, 0, len(parsed.Data.Tokens))
	var totalUsd float64

	for _, t := range parsed.Data.Tokens {
		nm := networkMeta(t.Network)
		native := t.TokenAddress == "" || strings.EqualFold(t.TokenAddress, "native")

		decimals := 18
		symbol, name, logo := "", "", ""
		if t.TokenMetadata != nil {
			if t.TokenMetadata.Decimals != nil {
				decimals = *t.TokenMetadata.Decimals
			}
			symbol = t.TokenMetadata.Symbol
			name = t.TokenMetadata.Name
			logo = t.TokenMetadata.Logo
		}
		if native {
			decimals = 18
			if symbol == "" {
				symbol = nm.nativeSymbol
			}
			if name == "" {
				name = nm.nativeName
			}
		}

		// Alchemy rarely returns a logo — fill from the Trust Wallet CDN.
		if logo == "" {
			logo = tokenLogo(nm.chain, t.TokenAddress, native)
		}

		uiAmount := hexBalanceToFloat(t.TokenBalance, decimals)
		if uiAmount <= 0 {
			continue // dust / empty position
		}

		var priceUsd float64
		for _, p := range t.TokenPrices {
			if strings.EqualFold(p.Currency, "usd") {
				priceUsd = parseFloat(p.Value)
				break
			}
		}
		valueUsd := uiAmount * priceUsd
		spam := looksSpam(symbol, name, native, priceUsd)
		if !spam {
			totalUsd += valueUsd // spam never counts toward the total
		}

		addr := t.TokenAddress
		if native {
			addr = "native"
		}
		tokens = append(tokens, evmToken{
			Chain: nm.chain, Network: t.Network, Address: addr,
			Symbol: symbol, Name: name, Decimals: decimals, Logo: logo,
			UIAmount: uiAmount, PriceUsd: priceUsd, ValueUsd: valueUsd, Native: native, Spam: spam,
		})
	}

	// Highest USD value first; the wallet's biggest holdings lead.
	sort.SliceStable(tokens, func(i, j int) bool { return tokens[i].ValueUsd > tokens[j].ValueUsd })

	return map[string]any{
		"address":  address,
		"totalUsd": totalUsd,
		"tokens":   tokens,
	}
}

func looksLikeEVMAddress(s string) bool {
	if !strings.HasPrefix(s, "0x") || len(s) != 42 {
		return false
	}
	for _, c := range s[2:] {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) {
			return false
		}
	}
	return true
}

// hexBalanceToFloat converts a hex wei-style balance to a human amount, scaling
// by decimals. Uses big.Int/big.Float so 18-decimal values don't lose precision.
func hexBalanceToFloat(hexStr string, decimals int) float64 {
	hexStr = strings.TrimPrefix(strings.TrimSpace(hexStr), "0x")
	if hexStr == "" {
		return 0
	}
	bal, ok := new(big.Int).SetString(hexStr, 16)
	if !ok {
		return 0
	}
	if bal.Sign() == 0 {
		return 0
	}
	denom := new(big.Float).SetInt(new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(decimals)), nil))
	f := new(big.Float).Quo(new(big.Float).SetInt(bal), denom)
	v, _ := f.Float64()
	return v
}

func parseFloat(s string) float64 {
	var f float64
	if _, err := fmt.Sscanf(strings.TrimSpace(s), "%g", &f); err != nil {
		return 0
	}
	return f
}
