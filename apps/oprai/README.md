# OPRAI Frontend (Angular 19)

Chat interface, portfolio view, agent management, and admin panel for the OPRAI platform.

## Tech Stack

- **Angular 19** with standalone components
- **Signals** for reactive state
- **SCSS** with `--op-*` CSS custom properties design system
- **Angular Material** components
- Lazy-loaded feature modules

## Features

- **Chat** (`/`) — AI chat with Solana action parsing
- **Portfolio** (`/portfolio`) — Wallet holdings and token balances
- **Agents** (`/agents`) — AI agent management
- **Voice** (`/voice`) — Voice-based interactions
- **Admin** (`/admin`) — Admin panel (separate layout, bypasses gateway)

Legacy routes (`/market`, `/explore`, `/trade`, `/settings`, `/tokens`, `/nft`, `/defi`) redirect to `/`.

## Development

```bash
# Install dependencies
cd apps/oprai && npm install

# Dev server (port 3000)
npx ng serve --port 3000

# Build for production
npx ng build --configuration production

# Run tests
npx ng test

# Lint
npx ng lint
```

## Design System

All styling uses `--op-*` prefixed CSS tokens defined in `src/styles.scss`:

| Token | Purpose |
|-------|---------|
| `--op-bg-surface-1` | Primary background |
| `--op-bg-surface-2` | Card/elevated background |
| `--op-text-primary` | Primary text |
| `--op-text-secondary` | Secondary text |
| `--op-brand` | Brand color (Indigo `#5b5fc7` → Cyan `#06B6D4` gradient) |

Admin pages use a separate token system (`--bg-primary`, `--text-primary`).

## Project Structure

```
apps/oprai/
├── src/
│   ├── app/
│   │   ├── features/
│   │   │   ├── chat/              Chat interface + intent parser
│   │   │   ├── portfolio/         Wallet holdings
│   │   │   ├── agents/            Agent management
│   │   │   ├── voice/             Voice interactions
│   │   │   └── admin/             Admin panel (separate layout)
│   │   ├── core/
│   │   │   ├── services/          Auth, wallet, API, memory services
│   │   │   ├── guards/            Route guards
│   │   │   └── interceptors/      HTTP interceptors
│   │   └── shared/                Shared components
│   ├── styles.scss                Design tokens
│   ├── index.html
│   └── main.ts
├── public/                        Static assets
├── angular.json
├── tsconfig.json
├── tsconfig.app.json
├── tsconfig.spec.json
├── package.json
└── Dockerfile
```

## Key Services

| Service | Path | Description |
|---------|------|-------------|
| IntentParserService | `features/chat/services/` | Parses LLM action blocks into executable actions |
| AuthService | `core/services/` | SIWS auth flow, JWT management |
| WalletService | `core/services/` | Phantom wallet connection |
| ApiService | `core/services/` | HTTP client with JWT injection |
| MemoryService | `core/services/` | Memory/context management |

## Tests

- **Intent parser**: 19 tests (Karma + Jasmine)
- Action parsing (JSON + legacy format)
- QUERY parsing
- CLARIFY block parsing
- Combined parsing
