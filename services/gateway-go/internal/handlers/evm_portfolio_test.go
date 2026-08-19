package handlers

import (
	"encoding/json"
	"math"
	"testing"
)

func TestNormalizeEVMPortfolio(t *testing.T) {
	// Synthetic Alchemy response: 1 ETH native (@ $3000), 500 USDC (@ $1),
	// and a zero-balance token that must be dropped.
	// 0x0de0b6b3a7640000 = 1e18 (1 ETH); 0x1dcd6500 = 5e8 (500 USDC @ 6 dp).
	raw := `{"data":{"tokens":[
      {"address":"0xabc","network":"eth-mainnet","tokenAddress":"","tokenBalance":"0x0de0b6b3a7640000",
       "tokenMetadata":{"name":"Ethereum","symbol":"ETH","decimals":18,"logo":"http://x/eth.png"},
       "tokenPrices":[{"currency":"usd","value":"3000"}]},
      {"address":"0xabc","network":"base-mainnet","tokenAddress":"0xusdc","tokenBalance":"0x1dcd6500",
       "tokenMetadata":{"name":"USD Coin","symbol":"USDC","decimals":6},
       "tokenPrices":[{"currency":"usd","value":"1"}]},
      {"address":"0xabc","network":"arb-mainnet","tokenAddress":"0xdead","tokenBalance":"0x0",
       "tokenMetadata":{"name":"Dust","symbol":"DUST","decimals":18},"tokenPrices":[]}
    ]}}`

	var parsed alchemyTokensResponse
	if err := json.Unmarshal([]byte(raw), &parsed); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	out := normalizeEVMPortfolio("0xabc", &parsed)

	tokens := out["tokens"].([]evmToken)
	if len(tokens) != 2 {
		t.Fatalf("expected 2 tokens (dust dropped), got %d", len(tokens))
	}
	// Sorted by value desc: ETH ($3000) then USDC ($2500).
	if tokens[0].Symbol != "ETH" || !tokens[0].Native {
		t.Fatalf("first token should be native ETH, got %+v", tokens[0])
	}
	if math.Abs(tokens[0].UIAmount-1.0) > 1e-9 {
		t.Fatalf("ETH amount: got %v want 1.0", tokens[0].UIAmount)
	}
	if math.Abs(tokens[0].ValueUsd-3000) > 1e-6 {
		t.Fatalf("ETH value: got %v want 3000", tokens[0].ValueUsd)
	}
	if tokens[1].Symbol != "USDC" || math.Abs(tokens[1].UIAmount-500) > 1e-6 {
		t.Fatalf("USDC: got %+v", tokens[1])
	}
	if tokens[0].Address != "native" {
		t.Fatalf("native address label: got %q", tokens[0].Address)
	}
	total := out["totalUsd"].(float64)
	if math.Abs(total-3500) > 1e-6 {
		t.Fatalf("total: got %v want 3500", total)
	}
}

func TestLooksLikeEVMAddress(t *testing.T) {
	ok := "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
	if !looksLikeEVMAddress(ok) {
		t.Fatal("valid address rejected")
	}
	for _, bad := range []string{"", "0x123", "d8dA6BF26964aF9D7eEd9e03E53415D37aA96045", "0xZZ..."} {
		if looksLikeEVMAddress(bad) {
			t.Fatalf("bad address accepted: %q", bad)
		}
	}
}
