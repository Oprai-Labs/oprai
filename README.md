<div align="center">

<img src="packages/media/oprai_banner.svg" alt="OPRAI" width="820">

### Talk to your wallet.

OPRAI turns plain language into on-chain actions — swap, stake, lend, bridge,
launch a token — and hands each one to your wallet to sign.

[**Try it**](https://app.oprai.xyz) · [MIT](LICENSE)

</div>

---

## What it does

Ask for something, and OPRAI works out which protocol answers it, builds the
transaction, and shows you exactly what you are about to sign.

- **Trading** — routing, limit orders, DCA, perps
- **Yield and lending** — liquid staking, lending markets, LP positions
- **Cross-chain** — move value between Solana, Ethereum, Base, Arbitrum,
  Optimism, Polygon, BNB Chain and a dozen more
- **Tokens** — launch, burn, transfer, stream
- **NFTs** — listings, offers, collection and wallet analytics
- **Reading the chain** — portfolio, positions, holder distribution,
  smart-money flow, and other questions that come back as cards rather than
  paragraphs

Solana is where it runs deepest today — Jupiter, Kamino, Marinade, Jito,
Meteora, Raydium, Orca, Solend, pump.fun, Magic Eden, Tensor, Streamflow — and
bridging through Relay, deBridge and Squid reaches the rest.

Your keys never leave your wallet. Every transaction is built server-side,
signed client-side, and you see the full breakdown before approving.

## Quick start

**With Docker — no local toolchain needed:**

```bash
git clone https://github.com/Oprai-Labs/oprai.git && cd oprai
cp .env.example .env        # pre-filled; add your OPRAI_OPENAI_API_KEY
make docker-up              # builds and starts everything
```

Then open <http://localhost:3000>.

**From source**, if you want hot reload and per-service logs:

```bash
make install                # Node + Go deps
make build-python           # Python venvs
make proto                  # gRPC stubs
make dev-infra              # Postgres, Redis, Qdrant in Docker
make migrate
make dev-all                # every service, one terminal, colour-coded
make health
```

You will need Go 1.22+, Rust 1.77+, Python 3.12+, Node 20+, pnpm, `protoc` and
`honcho`. The first Rust build pulls a lot of crates — give it five minutes.

## How it works

A single gateway fronts every service. It validates the wallet session, injects
the caller's identity, and fans out over gRPC.

```
Angular  ──►  Gateway ──┬──►  Auth      wallet sign-in, sessions
                        ├──►  Chat      the model, tools, streaming
                        ├──►  Chain     quotes, transaction building, bridging
                        └──►  Memory    what it remembers about you

                    Postgres · Redis · Qdrant
```

**Signing in** is a wallet signature, not a password. The server hands out a
nonce, your wallet signs it, and the session lives in an HttpOnly cookie — the
token is never written to `localStorage`.

**Doing something on-chain** goes: your sentence → the model picks a tool →
a quote → an unsigned transaction → your wallet → the network. OPRAI never
holds a key and never submits anything you have not signed.

Full detail lives in [`CLAUDE.md`](CLAUDE.md).

## Repository map

```
proto/              gRPC contracts, by domain
services/
  gateway-go        the front door: auth, rate limits, circuit breaking
  auth-service-go   wallet sign-in, sessions, users
  chat-service-py   the model, tool routing, streaming
  solana-service-rs transaction building and bridging, every protocol
  memory-service-py long-term memory over Qdrant
  admin-service-go  operations panel backend
apps/oprai/         the whole frontend — chat, portfolio, admin
opraios/            standalone framework for building DeFi agents
agent-platform/     agent marketplace, its own sub-project
infra/              compose stack, Caddy, Prometheus, Grafana
docs/               technical documentation
```

## Commands

```bash
make dev-all        # everything, locally
make dev-go         # gateway + auth + admin
make dev-python     # chat + memory
make dev-rust       # solana-service
make dev-angular    # frontend only

make build-all      # proto + services + frontend
make test           # all test suites
make migrate        # database migrations
make backup         # pg_dump into backups/
make health         # aggregated service health
```

`make help` lists the rest.

## Configuration

Copy `.env.example` to `.env` — it ships with working defaults and generated
secrets. Three values you must supply yourself:

| Variable | Why |
|---|---|
| `OPRAI_OPENAI_API_KEY` | the model and embeddings |
| `OPRAI_JWT_SECRET` | signs user sessions |
| `OPRAI_INTERNAL_API_KEY` | proves the gateway is the gateway |

Everything else — RPC endpoint, market-data keys, model choice, ports — is
optional and documented inline in `.env.example`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Port already in use | `lsof -i :PORT`, or change it in `.env` |
| Go build fails | `go mod tidy` in the service directory |
| Python import errors | `make build-python` then `make proto` |
| Proto generation fails | `brew install protobuf` |
| Rust build feels stuck | it isn't — first build is 3–5 minutes |
| Want a clean slate | `make reset` (backs up first, then asks) |

## License

[MIT](LICENSE)
