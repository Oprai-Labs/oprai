# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| latest  | ✅        |

## Reporting a Vulnerability

**Do not report security vulnerabilities through public GitHub issues.**

Please report security vulnerabilities by emailing: **security@oprai.xyz**

Include the following in your report:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

You will receive a response within 48 hours. After initial reply we aim to:

- Confirm the issue within 72 hours
- Release a patch within 14 days for critical issues
- Credit you in the release notes (unless you prefer to remain anonymous)

## Scope

In scope:
- Authentication and authorization bypasses
- Injection vulnerabilities (SQL, command, prompt)
- Sensitive data exposure
- Cryptographic weaknesses
- Smart contract vulnerabilities

Out of scope:
- Denial of service attacks
- Social engineering
- Physical attacks
- Issues in third-party dependencies (report directly to them)

## Security Best Practices for Deployers

- Never commit `.env` files to version control
- Rotate all secrets before deploying to production
- Use `sslmode=require` or `sslmode=verify-full` for PostgreSQL in production
- Run services behind a reverse proxy (Nginx, Caddy) with TLS
- Enable firewall rules — only expose ports 80/443 publicly
- Keep Docker images and dependencies up to date
