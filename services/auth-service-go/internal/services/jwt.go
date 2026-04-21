package services

import (
	"fmt"
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
type JWTService struct {
	secret []byte
	ttl    time.Duration
}

// NewJWTService creates a JWTService with the given secret and TTL in seconds.
func NewJWTService(secret string, ttlSeconds int) *JWTService {
	return &JWTService{
		secret: []byte(secret),
		ttl:    time.Duration(ttlSeconds) * time.Second,
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
func (s *JWTService) Validate(tokenString string) (wallet string, expiresAt time.Time, err error) {
	token, err := jwt.ParseWithClaims(tokenString, &JWTClaims{}, func(token *jwt.Token) (any, error) {
		// Ensure the signing method is HMAC
		if _, ok := token.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, fmt.Errorf("unexpected signing method: %v", token.Header["alg"])
		}
		return s.secret, nil
	})
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
