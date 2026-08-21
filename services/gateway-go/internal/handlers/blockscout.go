package handlers

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"math/big"
	"net/http"
	"strconv"
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

// fetchRobinhoodTokens returns the wallet's native + ERC-20 holdings on Robinhood
// Chain as evmToken rows, plus their non-spam USD total.
func (m *MarketProxy) fetchRobinhoodTokens(r *http.Request, address string) ([]evmToken, float64) {
	tokens := make([]evmToken, 0, 8)
	var totalUsd float64

	// Native ETH balance + its USD price.
	if addr := m.blockscoutAddress(r, address); addr != nil {
		amt := bigStrToFloat(addr.CoinBalance, 18)
		if amt > 0 {
			price := parseFloat(addr.ExchangeRate)
			val := amt * price
			totalUsd += val
			tokens = append(tokens, evmToken{
				Chain: robinhoodChain, Network: robinhoodChain, Address: "native",
				Symbol: robinhoodNativeSymbol, Name: robinhoodNativeName, Decimals: 18,
				Logo: tokenLogo(robinhoodChain, "", true),
				UIAmount: amt, PriceUsd: price, ValueUsd: val, Native: true, Spam: false,
			})
		}
	}

	// ERC-20 balances (with metadata + exchange_rate from Blockscout).
	var balances []blockscoutTokenBalance
	if !m.blockscoutGet(r, fmt.Sprintf("/addresses/%s/token-balances", address), &balances) {
		return tokens, totalUsd
	}
	for _, b := range balances {
		if b.Token.Type != "" && b.Token.Type != "ERC-20" {
			continue // NFTs handled separately
		}
		decimals := 18
		if d, err := strconv.Atoi(strings.TrimSpace(b.Token.Decimals)); err == nil && d >= 0 && d <= 36 {
			decimals = d
		}
		amt := bigStrToFloat(b.Value, decimals)
		if amt <= 0 {
			continue
		}
		price := parseFloat(b.Token.ExchangeRate)
		val := amt * price
		spam := looksSpam(b.Token.Symbol, b.Token.Name, false, price)
		if !spam {
			totalUsd += val
		}
		logo := b.Token.IconURL
		if logo == "" {
			logo = tokenLogo(robinhoodChain, b.Token.AddressHash, false)
		}
		tokens = append(tokens, evmToken{
			Chain: robinhoodChain, Network: robinhoodChain, Address: b.Token.AddressHash,
			Symbol: b.Token.Symbol, Name: b.Token.Name, Decimals: decimals, Logo: logo,
			UIAmount: amt, PriceUsd: price, ValueUsd: val, Native: false, Spam: spam,
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
