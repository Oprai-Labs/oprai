package handlers

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log/slog"
	"math/big"
	"net/http"
	"sort"
	"strings"
	"time"
)

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
		totalUsd += valueUsd

		addr := t.TokenAddress
		if native {
			addr = "native"
		}
		tokens = append(tokens, evmToken{
			Chain: nm.chain, Network: t.Network, Address: addr,
			Symbol: symbol, Name: name, Decimals: decimals, Logo: logo,
			UIAmount: uiAmount, PriceUsd: priceUsd, ValueUsd: valueUsd, Native: native,
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
