package services

import (
	"encoding/hex"
	"fmt"
	"testing"

	"github.com/decred/dcrd/dcrec/secp256k1/v4"
	"github.com/decred/dcrd/dcrec/secp256k1/v4/ecdsa"
)

// privkey 0x…01 maps to this well-known Ethereum address. Validates that our
// keccak256 + address derivation matches the Ethereum spec independently of the
// recover path.
func TestEthAddressFromPubKey_KnownVector(t *testing.T) {
	var b [32]byte
	b[31] = 1
	priv := secp256k1.PrivKeyFromBytes(b[:])
	got := ethAddressFromPubKey(priv.PubKey())
	want := "7e5f4552091a69125d5dfcb7b8c2659029395bdf"
	if got != want {
		t.Fatalf("address derivation mismatch: got %s want %s", got, want)
	}
}

// Round-trip: sign an EIP-191 personal_sign hash with an independent key and
// confirm VerifyEVMSignature recovers to that key's address (and rejects a
// tampered message / wrong address).
func TestVerifyEVMSignature_RoundTrip(t *testing.T) {
	var b [32]byte
	b[31] = 42
	priv := secp256k1.PrivKeyFromBytes(b[:])
	address := "0x" + ethAddressFromPubKey(priv.PubKey())

	message := []byte("OPRAI link wallet: test-nonce-123")
	prefixed := []byte(fmt.Sprintf("\x19Ethereum Signed Message:\n%d", len(message)))
	prefixed = append(prefixed, message...)
	hash := keccak256(prefixed)

	// SignCompact returns [V(27+recid) || R || S]; Ethereum wants [R || S || V].
	compact := ecdsa.SignCompact(priv, hash, false)
	ethSig := make([]byte, 65)
	copy(ethSig[0:32], compact[1:33])
	copy(ethSig[32:64], compact[33:65])
	ethSig[64] = compact[0]
	sigHex := hex.EncodeToString(ethSig)

	if !VerifyEVMSignature(address, message, sigHex) {
		t.Fatal("valid signature rejected")
	}
	if VerifyEVMSignature(address, []byte("OPRAI link wallet: different"), sigHex) {
		t.Fatal("signature over a different message was accepted")
	}
	if VerifyEVMSignature("0x0000000000000000000000000000000000000001", message, sigHex) {
		t.Fatal("signature accepted for the wrong address")
	}
}

func TestNormalizeEVMAddress(t *testing.T) {
	if got := NormalizeEVMAddress("0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"); got != "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf" {
		t.Fatalf("normalize: got %s", got)
	}
	if NormalizeEVMAddress("not-an-address") != "" {
		t.Fatal("bad address should normalize to empty")
	}
}
