package handlers

import (
	"strings"
	"testing"
)

func TestNormalizeHeliusTx(t *testing.T) {
	t.Parallel()

	item := normalizeHeliusTx(heliusTx{
		Signature:   "sig1",
		Timestamp:   1700000000,
		Type:        "SWAP",
		Source:      "JUPITER",
		Description: "Swap SOL for USDC",
		Fee:         5000,
		NativeTransfers: []heliusTransfer{
			{
				Amount:          1_000_000_000,
				FromUserAccount: "wallet_a",
				ToUserAccount:   "wallet_b",
			},
		},
	}, "wallet_a")

	if item.Signature != "sig1" {
		t.Fatalf("unexpected signature: %s", item.Signature)
	}
	if item.Type != "swap" {
		t.Fatalf("unexpected normalized type: %s", item.Type)
	}
	if item.Platform != "jupiter" {
		t.Fatalf("unexpected normalized platform: %s", item.Platform)
	}
	if item.ValueSol != -1 {
		t.Fatalf("expected -1 SOL net value, got %f", item.ValueSol)
	}
	if !item.Success {
		t.Fatal("expected success=true for nil transaction error")
	}
}

func TestShouldHideSpamTx(t *testing.T) {
	t.Parallel()

	if !shouldHideSpamTx(accountTxItem{Type: "nft_airdrop"}) {
		t.Fatal("expected nft_airdrop to be hidden")
	}
	if !shouldHideSpamTx(accountTxItem{Description: "Free spam rewards"}) {
		t.Fatal("expected spam description to be hidden")
	}
	if shouldHideSpamTx(accountTxItem{Type: "swap", Description: "Regular swap"}) {
		t.Fatal("did not expect regular swap to be hidden")
	}
}


func TestRpcMethodBlocked_BatchElementCap(t *testing.T) {
	mkBatch := func(n int) []byte {
		parts := make([]string, n)
		for i := range parts {
			parts[i] = `{"jsonrpc":"2.0","id":1,"method":"getAccountInfo","params":["x"]}`
		}
		return []byte("[" + strings.Join(parts, ",") + "]")
	}
	// A batch of 50 cheap allowed calls is permitted.
	if reason := rpcMethodBlocked(mkBatch(50)); reason != "" {
		t.Fatalf("batch of 50 should be allowed, got blocked: %q", reason)
	}
	// A batch of 51 is rejected purely on count (all methods are individually allowed,
	// so before the cap this returned "" — i.e. the amplification vector was open).
	if reason := rpcMethodBlocked(mkBatch(51)); reason == "" {
		t.Fatal("batch of 51 should be rejected by the element cap, but was allowed")
	}
	if reason := rpcMethodBlocked(mkBatch(500)); reason == "" {
		t.Fatal("batch of 500 should be rejected by the element cap, but was allowed")
	}
	// A single call is unaffected.
	if reason := rpcMethodBlocked([]byte(`{"jsonrpc":"2.0","method":"getAccountInfo","params":[]}`)); reason != "" {
		t.Fatalf("single call should be allowed, got: %q", reason)
	}
}
