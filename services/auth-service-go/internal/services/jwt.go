package services

import (
	"fmt"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

// JWTResult is returned from Issue.
type JWTResult struct {
	Token     string `json:"token"`
	ExpiresAt string `json:"expiresAt"`
	JTI       string `json:"-"` // token ID, not exposed to clients
}

// JWTClaims are the custom claims embedded in OPRAI JWTs.
// The "w" field holds the wallet address, matching the Node.js format.
// RegisteredClaims.ID carries the unique jti for token revocation.
type JWTClaims struct {
	Wallet string `json:"w"`
	jwt.RegisteredClaims
}

// JWTService handles JWT issuance and validation.
//
// Two-secret rolling rotation:
//   - `secret` is the *current* signing secret. All NEW tokens are signed with
//     this. Validation tries this first.
//   - `previousSecret` (optional) is the secret that was current before the
//     last rotation. Validation falls back to it so tokens issued before
//     rotation continue to work until they expire naturally. Set to "" when
//     not rotating.
//
// Rotation procedure:
//
//	1. Set OPRAI_JWT_SECRET_OLD to the value of OPRAI_JWT_SECRET in the
//	   service's env / secret store.
//	2. Set OPRAI_JWT_SECRET to a freshly generated value.
//	3. Restart the service. Tokens issued before step 2 still validate
//	   against OPRAI_JWT_SECRET_OLD; new tokens use the new secret.
//	4. After the longest token TTL has elapsed (3 days by default), unset
//	   OPRAI_JWT_SECRET_OLD and restart again to retire the old secret.
type JWTService struct {
	secret         []byte
	previousSecret []byte
	ttl            time.Duration
}

// NewJWTService creates a JWTService with the given secret and TTL in seconds.
// previousSecret may be empty when no rotation is in flight.
func NewJWTService(secret, previousSecret string, ttlSeconds int) *JWTService {
	var prev []byte
	if previousSecret != "" {
		prev = []byte(previousSecret)
	}
	return &JWTService{
		secret:         []byte(secret),
		previousSecret: prev,
		ttl:            time.Duration(ttlSeconds) * time.Second,
	}
}

// Issue creates a new signed JWT for the given wallet address.
// Each token receives a unique jti (JWT ID) so it can be individually revoked.
func (s *JWTService) Issue(walletAddress string) (*JWTResult, error) {
	now := time.Now()
	expiresAt := now.Add(s.ttl)
	jti := uuid.New().String()

	claims := JWTClaims{
		Wallet: walletAddress,
		RegisteredClaims: jwt.RegisteredClaims{
			ID:        jti,
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(expiresAt),
		},
	}

	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	signed, err := token.SignedString(s.secret)
	if err != nil {
		return nil, fmt.Errorf("signing JWT: %w", err)
	}

	return &JWTResult{
		Token:     signed,
		ExpiresAt: expiresAt.UTC().Format(time.RFC3339),
		JTI:       jti,
	}, nil
}

// ParseUnvalidated extracts the jti from a token string without validating the
// signature. Used only in logout to revoke the caller's own token.
func (s *JWTService) ParseUnvalidated(tokenString string) (jti string, expiresAt time.Time, err error) {
	p := jwt.NewParser()
	claims := &JWTClaims{}
	token, _, err := p.ParseUnverified(tokenString, claims)
	if err != nil {
		return "", time.Time{}, fmt.Errorf("parsing token: %w", err)
	}
	_ = token
	jti = claims.RegisteredClaims.ID
	if claims.ExpiresAt != nil {
		expiresAt = claims.ExpiresAt.Time
	}
	return jti, expiresAt, nil
}

// Validate parses and validates a JWT token string.
// Returns the wallet address and expiration time, or an error.
//
// During key rotation, the current secret is tried first; if signature
// verification fails AND a previous secret is configured, we retry against it
// so tokens issued under the old key continue to work until they expire.
func (s *JWTService) Validate(tokenString string) (wallet string, expiresAt time.Time, err error) {
	parse := func(secret []byte) (*jwt.Token, error) {
		return jwt.ParseWithClaims(tokenString, &JWTClaims{}, func(token *jwt.Token) (any, error) {
			if _, ok := token.Method.(*jwt.SigningMethodHMAC); !ok {
				return nil, fmt.Errorf("unexpected signing method: %v", token.Header["alg"])
			}
			return secret, nil
		})
	}

	token, err := parse(s.secret)
	if err != nil && len(s.previousSecret) > 0 {
		// Retry against the previous secret only if the failure looks like a
		// signature mismatch (`ErrTokenSignatureInvalid` from jwt/v5). Any
		// other failure (expiry, malformed payload) is structural and won't
		// be helped by a different key.
		if errIsSignatureInvalid(err) {
			token, err = parse(s.previousSecret)
		}
	}
	if err != nil {
		return "", time.Time{}, fmt.Errorf("parsing token: %w", err)
	}

	claims, ok := token.Claims.(*JWTClaims)
	if !ok || !token.Valid {
		return "", time.Time{}, fmt.Errorf("invalid token claims")
	}

	if claims.Wallet == "" {
		return "", time.Time{}, fmt.Errorf("missing wallet in token")
	}

	exp := time.Time{}
	if claims.ExpiresAt != nil {
		exp = claims.ExpiresAt.Time
	}

	return claims.Wallet, exp, nil
}

func errIsSignatureInvalid(err error) bool {
	if err == nil {
		return false
	}
	msg := err.Error()
	return strings.Contains(msg, "signature is invalid") ||
		strings.Contains(msg, "signature mismatch")
}
