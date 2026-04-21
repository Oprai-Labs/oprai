package services

import (
	"crypto/ed25519"
	"log/slog"

	"github.com/mr-tron/base58"
)

// VerifySignature verifies an ed25519 signature produced by a Solana wallet.
//
// Parameters:
//   - walletBase58: the signer's public key in base58 encoding
//   - message: the raw message bytes that were signed
//   - signatureBase58: the signature in base58 encoding
//
// Returns true if the signature is valid.
func VerifySignature(walletBase58 string, message []byte, signatureBase58 string) bool {
	pubKeyBytes, err := base58.Decode(walletBase58)
	if err != nil {
		slog.Warn("Failed to decode wallet base58",
			"wallet", walletBase58,
			"error", err,
		)
		return false
	}

	if len(pubKeyBytes) != ed25519.PublicKeySize {
		slog.Warn("Invalid public key length",
			"wallet", walletBase58,
			"got", len(pubKeyBytes),
			"expected", ed25519.PublicKeySize,
		)
		return false
	}

	sigBytes, err := base58.Decode(signatureBase58)
	if err != nil {
		slog.Warn("Failed to decode signature base58",
			"wallet", walletBase58,
			"error", err,
		)
		return false
	}

	if len(sigBytes) != ed25519.SignatureSize {
		slog.Warn("Invalid signature length",
			"wallet", walletBase58,
			"got", len(sigBytes),
			"expected", ed25519.SignatureSize,
		)
		return false
	}

	return ed25519.Verify(ed25519.PublicKey(pubKeyBytes), message, sigBytes)
}
