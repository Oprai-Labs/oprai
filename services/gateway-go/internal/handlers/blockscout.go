package handlers

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log/slog"
	"math/big"
	"net/http"
	"os"
	"strings"
)

// Robinhood Chain reads via its Blockscout explorer. Alchemy/Moralis do not
// index chain 4663, but Robinhood ships a public Blockscout with a full v2 API
// (token balances WITH USD prices, native balance, txs, NFTs) — free, no key —
// so it needs no node of ours. These fetchers return the SAME shapes the Alchemy/
// Moralis paths do, and the EVM handlers merge them into the multichain response.

const (
	robinhoodChain        = "robinhood"
	blockscoutBase        = "https://robinhoodchain.blockscout.com/api/v2"
	robinhoodNativeSymbol = "ETH"
	robinhoodNativeName   = "Ether"
)

// The surfaces the readers below actually implement. Kept beside them so that
// implementing a reader without advertising it — or advertising one that was
// never written — is visible in one place. There is no DeFi-position provider
// for this chain, which is why "positions" is absent.
var robinhoodReads = []string{"balances", "transactions", "nfts"}

// bigStrToFloat scales a base-unit decimal string by 10^decimals (big.Float, no
// precision loss on 18-decimal values). Blockscout returns balances as decimal
// strings, not hex.
func bigStrToFloat(dec string, decimals int) float64 {
	dec = strings.TrimSpace(dec)
	if dec == "" {
		return 0
	}
	bal, ok := new(big.Int).SetString(dec, 10)
	if !ok || bal.Sign() == 0 {
		return 0
	}
	denom := new(big.Float).SetInt(new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(decimals)), nil))
	f := new(big.Float).Quo(new(big.Float).SetInt(bal), denom)
	v, _ := f.Float64()
	return v
}

type blockscoutAddress struct {
	CoinBalance  string `json:"coin_balance"`
	ExchangeRate string `json:"exchange_rate"`
}

type blockscoutTokenBalance struct {
	Value string `json:"value"`
	Token struct {
		AddressHash  string `json:"address_hash"`
		Symbol       string `json:"symbol"`
		Name         string `json:"name"`
		Decimals     string `json:"decimals"`
		ExchangeRate string `json:"exchange_rate"`
		IconURL      string `json:"icon_url"`
		Type         string `json:"type"`
	} `json:"token"`
}

// Robinhood JSON-RPC. Blockscout's REST API Cloudflare-blocks datacenter IPs
// (403 from the server), so balances came back empty and the chat never saw a
// user's Robinhood holdings. The chain's JSON-RPC node is NOT blocked, so read
// balances there instead: native via eth_getBalance, ERC-20 via balanceOf over a
// curated list (Alchemy doesn't index 4663, so there's no enumeration API — the
// tokens that matter on Robinhood are few and known).
const robinhoodRPCDefault = "https://rpc.mainnet.chain.robinhood.com"

func robinhoodRPCURL() string {
	if v := strings.TrimSpace(os.Getenv("ROBINHOOD_RPC")); v != "" {
		return v
	}
	return robinhoodRPCDefault
}

// robinhoodRPC makes one JSON-RPC call and returns the `result` string.
func (m *MarketProxy) robinhoodRPC(r *http.Request, method string, params []any) (string, bool) {
	reqBody, err := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
	if err != nil {
		return "", false
	}
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, robinhoodRPCURL(), bytes.NewReader(reqBody))
	if err != nil {
		return "", false
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := m.client.Do(req)
	if err != nil {
		slog.Warn("robinhood rpc error", "method", method, "error", err)
		return "", false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		slog.Warn("robinhood rpc non-200", "method", method, "status", resp.StatusCode)
		return "", false
	}
	var out struct {
		Result string `json:"result"`
		Error  *struct {
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil || out.Error != nil {
		return "", false
	}
	return out.Result, true
}

// hexToFloat scales a 0x-hex quantity by 10^decimals.
func hexToFloat(hexStr string, decimals int) float64 {
	hexStr = strings.TrimPrefix(strings.TrimSpace(hexStr), "0x")
	if hexStr == "" {
		return 0
	}
	bal, ok := new(big.Int).SetString(hexStr, 16)
	if !ok || bal.Sign() == 0 {
		return 0
	}
	denom := new(big.Float).SetInt(new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(decimals)), nil))
	f := new(big.Float).Quo(new(big.Float).SetInt(bal), denom)
	v, _ := f.Float64()
	return v
}

// fetchRobinhoodTokens returns the wallet's native + curated ERC-20 holdings on
// Robinhood Chain (via JSON-RPC), plus their USD total. `ethPrice` is the live
// mainnet ETH price (passed in from the Alchemy portfolio) — Robinhood ETH is the
// bridged same asset. USDG/USDe are dollar-pegged, so priced at $1.
func (m *MarketProxy) fetchRobinhoodTokens(r *http.Request, address string, ethPrice float64) ([]evmToken, float64) {
	tokens := make([]evmToken, 0, 4)
	var totalUsd float64

	if res, ok := m.robinhoodRPC(r, "eth_getBalance", []any{address, "latest"}); ok {
		if amt := hexToFloat(res, 18); amt > 0 {
			val := amt * ethPrice
			totalUsd += val
			tokens = append(tokens, evmToken{
				Chain: robinhoodChain, Network: robinhoodChain, Address: "native",
				Symbol: robinhoodNativeSymbol, Name: robinhoodNativeName, Decimals: 18,
				Logo: tokenLogo(robinhoodChain, "", true),
				UIAmount: amt, PriceUsd: ethPrice, ValueUsd: val, Native: true,
			})
		}
	}

	padded := "000000000000000000000000" + strings.ToLower(strings.TrimPrefix(address, "0x"))
	curated := []struct {
		symbol, name, addr string
		decimals           int
		price              float64
	}{
		{"USDG", "Global Dollar", "0x5fc5360d0400a0fd4f2af552add042d716f1d168", 6, 1.0},
		{"USDe", "Ethena USDe", "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34", 18, 1.0},
		{"WETH", "Wrapped Ether", "0x0bd7d308f8e1639fab988df18a8011f41eacad73", 18, ethPrice},
	}
	for _, t := range curated {
		res, ok := m.robinhoodRPC(r, "eth_call", []any{map[string]string{"to": t.addr, "data": "0x70a08231" + padded}, "latest"})
		if !ok {
			continue
		}
		amt := hexToFloat(res, t.decimals)
		if amt <= 0 {
			continue
		}
		val := amt * t.price
		totalUsd += val
		tokens = append(tokens, evmToken{
			Chain: robinhoodChain, Network: robinhoodChain, Address: t.addr,
			Symbol: t.symbol, Name: t.name, Decimals: t.decimals,
			Logo: tokenLogo(robinhoodChain, t.addr, false),
			UIAmount: amt, PriceUsd: t.price, ValueUsd: val, Native: false,
		})
	}
	return tokens, totalUsd
}

type blockscoutTx struct {
	Hash      string `json:"hash"`
	Timestamp string `json:"timestamp"`
	Method    string `json:"method"`
	Result    string `json:"result"`
	Status    string `json:"status"`
	From      struct {
		Hash string `json:"hash"`
	} `json:"from"`
	To struct {
		Hash       string `json:"hash"`
		Name       string `json:"name"`
		IsContract bool   `json:"is_contract"`
	} `json:"to"`
	TransactionTypes []string `json:"transaction_types"`
}

// fetchRobinhoodTxs returns the wallet's recent Robinhood Chain transactions.
func (m *MarketProxy) fetchRobinhoodTxs(r *http.Request, address string) []evmTx {
	var page struct {
		Items []blockscoutTx `json:"items"`
	}
	if !m.blockscoutGet(r, fmt.Sprintf("/addresses/%s/transactions", address), &page) {
		return nil
	}
	out := make([]evmTx, 0, len(page.Items))
	for _, t := range page.Items {
		category := t.Method
		if category == "" && len(t.TransactionTypes) > 0 {
			category = t.TransactionTypes[0]
		}
		summary := t.Method
		if summary == "" {
			summary = "Transaction"
		}
		platform := ""
		if t.To.IsContract {
			platform = t.To.Name
		}
		direction := "in"
		if strings.EqualFold(t.From.Hash, address) {
			direction = "out"
		}
		out = append(out, evmTx{
			Hash: t.Hash, Chain: robinhoodChain, Timestamp: t.Timestamp,
			Category: category, Summary: summary, Platform: platform,
			Direction: direction, Success: t.Status == "ok" || t.Result == "success",
		})
	}
	return out
}

type blockscoutNft struct {
	ID       string `json:"id"`
	ImageURL string `json:"image_url"`
	MediaURL string `json:"media_url"`
	Metadata struct {
		Name string `json:"name"`
	} `json:"metadata"`
	Token struct {
		Name string `json:"name"`
		Type string `json:"type"`
	} `json:"token"`
}

// fetchRobinhoodNfts returns the wallet's Robinhood Chain NFTs.
func (m *MarketProxy) fetchRobinhoodNfts(r *http.Request, address string) []evmNft {
	var page struct {
		Items []blockscoutNft `json:"items"`
	}
	if !m.blockscoutGet(r, fmt.Sprintf("/addresses/%s/nft?type=ERC-721%%2CERC-1155%%2CERC-404", address), &page) {
		return nil
	}
	out := make([]evmNft, 0, len(page.Items))
	for _, n := range page.Items {
		name := n.Metadata.Name
		if name == "" {
			name = strings.TrimSpace(n.Token.Name + " #" + n.ID)
		}
		image := n.ImageURL
		if image == "" {
			image = n.MediaURL
		}
		out = append(out, evmNft{
			Chain: robinhoodChain, Name: name, Collection: n.Token.Name,
			Image: image, TokenID: n.ID,
		})
	}
	return out
}

// blockscoutAddress fetches an address's native balance + exchange rate.
func (m *MarketProxy) blockscoutAddress(r *http.Request, address string) *blockscoutAddress {
	var a blockscoutAddress
	if !m.blockscoutGet(r, "/addresses/"+address, &a) {
		return nil
	}
	return &a
}

// blockscoutGet GETs a Blockscout v2 path and decodes JSON into dst. Returns
// false (and logs) on any failure — Robinhood is one chain among many, so a miss
// must never fail the whole multichain response.
func (m *MarketProxy) blockscoutGet(r *http.Request, path string, dst any) bool {
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, blockscoutBase+path, nil)
	if err != nil {
		return false
	}
	req.Header.Set("Accept", "application/json")
	resp, err := m.client.Do(req)
	if err != nil {
		slog.Warn("blockscout error", "path", path, "error", err)
		return false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		slog.Warn("blockscout non-200", "path", path, "status", resp.StatusCode)
		return false
	}
	if err := json.NewDecoder(resp.Body).Decode(dst); err != nil {
		slog.Warn("blockscout decode", "path", path, "error", err)
		return false
	}
	return true
}
