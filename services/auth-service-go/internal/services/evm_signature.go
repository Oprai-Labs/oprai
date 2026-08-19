package services

import (
	"encoding/hex"
	"fmt"
	"strings"

	"github.com/decred/dcrd/dcrec/secp256k1/v4"
	"github.com/decred/dcrd/dcrec/secp256k1/v4/ecdsa"
	"golang.org/x/crypto/sha3"
)

// VerifyEVMSignature verifies an EIP-191 `personal_sign` signature over message
// and returns true iff it recovers to address (a 0x-hex EVM address, compared
// case-insensitively). This is the EVM analogue of VerifySignature (ed25519):
// it proves the caller controls the private key of the wallet they want to link.
//
// The signature is the standard 65-byte [R || S || V] produced by
// eth_personalSign / wallet `signMessage`, hex-encoded (with or without 0x).
func VerifyEVMSignature(address string, message []byte, sigHex string) bool {
	addr := strings.ToLower(strings.TrimPrefix(strings.TrimSpace(address), "0x"))
	if len(addr) != 40 || !isHex(addr) {
		return false
	}

	sig, err := hex.DecodeString(strings.TrimPrefix(strings.TrimSpace(sigHex), "0x"))
	if err != nil || len(sig) != 65 {
		return false
	}

	// EIP-191: hash = keccak256("\x19Ethereum Signed Message:\n" + len + message)
	prefixed := []byte(fmt.Sprintf("\x19Ethereum Signed Message:\n%d", len(message)))
	prefixed = append(prefixed, message...)
	hash := keccak256(prefixed)

	// Ethereum places the recovery id V last, as 27/28 (or 0/1 for some wallets).
	// decred's RecoverCompact wants it FIRST, encoded as 27+recid (uncompressed).
	v := sig[64]
	if v >= 27 {
		v -= 27
	}
	if v != 0 && v != 1 {
		return false
	}
	compact := make([]byte, 65)
	compact[0] = 27 + v
	copy(compact[1:33], sig[0:32])
	copy(compact[33:65], sig[32:64])

	pub, _, err := ecdsa.RecoverCompact(compact, hash)
	if err != nil {
		return false
	}
	return ethAddressFromPubKey(pub) == addr
}

func keccak256(data []byte) []byte {
	h := sha3.NewLegacyKeccak256()
	h.Write(data)
	return h.Sum(nil)
}

// ethAddressFromPubKey derives the lowercase 40-char hex address (no 0x) from a
// secp256k1 public key: keccak256 of the 64-byte uncompressed body, last 20.
func ethAddressFromPubKey(pub *secp256k1.PublicKey) string {
	uncompressed := pub.SerializeUncompressed() // 0x04 || X || Y
	hash := keccak256(uncompressed[1:])
	return hex.EncodeToString(hash[12:])
}

func isHex(s string) bool {
	for _, c := range s {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			return false
		}
	}
	return true
}

// NormalizeEVMAddress lowercases and 0x-prefixes an EVM address for storage.
// Returns "" if it is not a 20-byte hex address.
func NormalizeEVMAddress(address string) string {
	addr := strings.ToLower(strings.TrimPrefix(strings.TrimSpace(address), "0x"))
	if len(addr) != 40 || !isHex(addr) {
		return ""
	}
	return "0x" + addr
}
