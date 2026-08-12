# Angular Frontend (apps/oprai/)

Angular 19 standalone components, signals, lazy-loaded feature modules. OPRAI's user interface — chat, portfolio, agents, voice, admin.

## Quick Start

```bash
cd apps/oprai
pnpm install
npx ng serve --port 3000
# → http://localhost:3000
```

## Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │        ANGULAR APP (:3000)           │
                                    │                                     │
                                    │  ┌─────────────────────────────┐    │
                                    │  │     MainLayoutComponent     │    │
                                    │  │   (Sidebar + Header +      │    │
                                    │  │    RouterOutlet)           │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    │  ┌──────────────┴──────────────┐    │
                                    │  │    Lazy Feature Modules     │    │
                                    │  │  ┌───────┬───────┬───────┐  │    │
                                    │  │  │ Chat  │Portfolio│Admin│  │    │
                                    │  │  └───────┴───────┴───────┘  │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    └─────────────────┼───────────────────┘
                                                      │
                                         ┌────────────┴────────────┐
                                         │                         │
                                         ▼                         ▼
                                  ┌─────────────┐           ┌─────────────┐
                                  │  Gateway    │           │ Admin Service│
                                  │   :3001     │           │    :3050    │
                                  └─────────────┘           └─────────────┘
```

---

## File Structure

```
apps/oprai/
├── src/
│   ├── main.ts                    # Bootstrap entry
│   ├── index.html
│   ├── styles.scss                # Global CSS + design tokens
│   │
│   ├── app/
│   │   ├── app.component.ts       # Root component
│   │   ├── app.routes.ts          # Top-level routing
│   │   ├── app.config.ts          # Application config
│   │   │
│   │   ├── core/                  # Singleton services
│   │   │   ├── services/
│   │   │   │   ├── auth.service.ts          # SIWS auth flow
│   │   │   │   ├── wallet.service.ts        # Wallet adapter management
│   │   │   │   ├── api.service.ts           # HTTP + SSE client
│   │   │   │   ├── memory.service.ts        # Long-term memory API
│   │   │   │   ├── session-storage.service.ts # Chat session state
│   │   │   │   ├── upload.service.ts        # IPFS image upload
│   │   │   │   ├── liquidation-monitor.service.ts # DeFi position alerts
│   │   │   │   └── scheduler.service.ts     # Scheduled action reminders
│   │   │   │
│   │   │   ├── guards/
│   │   │   │   └── auth.guard.ts            # Route protection
│   │   │   │
│   │   │   └── interceptors/
│   │   │       └── auth.interceptor.ts      # JWT injection
│   │   │
│   │   ├── features/              # Feature modules (lazy-loaded)
│   │   │   ├── chat/
│   │   │   │   ├── chat.routes.ts
│   │   │   │   ├── pages/
│   │   │   │   │   └── chat-shell/
│   │   │   │   │       ├── chat-shell.component.ts
│   │   │   │   │       ├── chat-shell.component.html
│   │   │   │   │       └── chat-shell.component.scss
│   │   │   │   ├── services/
│   │   │   │   │   ├── chat-api.service.ts       # Chat HTTP/SSE
│   │   │   │   │   ├── intent-parser.service.ts  # [ACTION]/[QUERY] parsing
│   │   │   │   │   └── solana-action.service.ts  # TX building + execution
│   │   │   │   └── components/
│   │   │   │       ├── message-list/
│   │   │   │       ├── message-composer/
│   │   │   │       └── action-card/
│   │   │   │
│   │   │   ├── portfolio/
│   │   │   │   ├── portfolio.routes.ts
│   │   │   │   ├── pages/
│   │   │   │   │   └── portfolio-shell/
│   │   │   │   ├── services/
│   │   │   │   │   ├── portfolio.service.ts      # Token/NFT/DeFi aggregation
│   │   │   │   │   ├── solana-rpc.service.ts     # Direct RPC calls
│   │   │   │   │   ├── birdeye.service.ts        # Price data
│   │   │   │   │   ├── helius.service.ts         # Enhanced TX parsing
│   │   │   │   │   └── defi-positions.service.ts # Protocol positions
│   │   │   │   └── components/
│   │   │   │       ├── portfolio-overview/
│   │   │   │       ├── token-list/
│   │   │   │       ├── nft-gallery/
│   │   │   │       └── recent-activity/
│   │   │   │
│   │   │   ├── projects/          # Agents feature (alias: /agents)
│   │   │   │   └── projects.routes.ts
│   │   │   │
│   │   │   ├── voice/
│   │   │   │   └── voice.routes.ts
│   │   │   │
│   │   │   └── admin/
│   │   │       ├── admin.routes.ts
│   │   │       └── pages/
│   │   │
│   │   ├── layout/
│   │   │   ├── main-layout/
│   │   │   │   ├── main-layout.component.ts
│   │   │   │   ├── sidebar.component.ts
│   │   │   │   └── header.component.ts
│   │   │   └── admin-layout/
│   │   │
│   │   └── shared/                # Shared components
│   │       └── components/
│   │           ├── skeletons/
│   │           └── wallet-connect/
│   │
│   └── environments/
│       ├── environment.ts         # Development
│       └── environment.prod.ts    # Production
│
├── angular.json
├── package.json
└── tsconfig.json
```

---

## Environment Variables

```typescript
// environment.ts
export const environment = {
  production: false,
  apiBase: 'http://localhost:3001',        // Gateway
  apiUrl: 'http://localhost:3001',
  adminApiBase: 'http://localhost:3050',   // Admin service (bypasses gateway)
  solanaNetwork: 'mainnet-beta',
  solanaRpc: 'http://localhost:3001/rpc',  // RPC proxied through gateway
  heliusRpcUrl: 'http://localhost:3001/rpc',
};
```

**Note:** API keys are not in the client bundle — all external calls go through the gateway.

---

## Routing

### App Routes

```typescript
// app.routes.ts
export const appRoutes: Routes = [
  // Admin — separate layout, bypasses gateway
  {
    path: 'admin',
    loadChildren: () => import('./features/admin/admin.routes'),
  },

  // Main app — shared layout with sidebar
  {
    path: '',
    component: MainLayoutComponent,
    children: [
      { path: '', loadChildren: () => import('./features/chat/chat.routes') },
      { path: 'portfolio', loadChildren: () => import('./features/portfolio/portfolio.routes') },
      { path: 'agents', loadChildren: () => import('./features/projects/projects.routes') },
      { path: 'voice', loadChildren: () => import('./features/voice/voice.routes') },

      // Legacy redirects
      { path: 'market', redirectTo: '/' },
      { path: 'explore', redirectTo: '/' },
      { path: 'trade', redirectTo: '/' },
      { path: 'settings', redirectTo: '/' },
      { path: 'tokens', redirectTo: '/' },
      { path: 'nft', redirectTo: '/' },
      { path: 'defi', redirectTo: '/' },
    ],
  },

  { path: '**', redirectTo: '/' },
];
```

### Chat Routes

```typescript
// features/chat/chat.routes.ts
export const CHAT_ROUTES: Routes = [
  { path: '', loadComponent: () => import('./pages/chat-shell/chat-shell.component') },
  { path: 'c/:sessionId', loadComponent: () => import('./pages/chat-shell/chat-shell.component') },
];
```

---

## Core Services

### AuthService — SIWS Flow

```typescript
// core/services/auth.service.ts
@Injectable({ providedIn: 'root' })
export class AuthService {
  readonly authenticated = signal(false);
  readonly authenticating = signal(false);
  readonly publicKey = signal<string | null>(null);

  // SIWS flow
  async authenticate(): Promise<void> {
    // 1. Get nonce from auth-service
    const { nonce, nonceId } = await this.api.getNonce();

    // 2. Request wallet signature
    const message = `OPRAI login: ${nonce}`;
    const signature = await this.walletService.signMessage(message);

    // 3. Verify signature → get JWT
    const { token } = await this.api.verifySignature(publicKey, signature, nonceId);

    // 4. Store JWT
    localStorage.setItem('oprai-auth-token', token);
    this.authenticated.set(true);
  }

  isAuthenticated(): boolean {
    const token = localStorage.getItem('oprai-auth-token');
    if (!token) return false;
    // Decode + check expiry
    return !this.isTokenExpired(token);
  }
}
```

**JWT Claims:**
```json
{
  "w": "Hx7b8kL9mN2pQ4rS6tU8vW0xY2zA4bC6d",
  "iat": 1704067200,
  "exp": 1704326400
}
```

---

### WalletService — Wallet Adapter Management

```typescript
// core/services/wallet.service.ts
@Injectable({ providedIn: 'root' })
export class WalletService {
  readonly connected = signal(false);
  readonly publicKey = signal<string | null>(null);
  readonly walletName = signal<string | null>(null);

  // Supported wallets
  private readonly SUPPORTED_WALLETS = [
    { name: 'Phantom', adapter: PhantomWalletAdapter },
    { name: 'Solflare', adapter: SolflareWalletAdapter },
    { name: 'Backpack', adapter: BackpackWalletAdapter },
    { name: 'Coinbase Wallet', adapter: CoinbaseWalletAdapter },
    { name: 'OKX Wallet', adapter: OKXWalletAdapter },
    { name: 'Magic Eden', adapter: MagicEdenAdapter },
  ];

  async connect(walletName: string): Promise<string> { ... }
  async disconnect(): Promise<void> { ... }
  async signMessage(message: string): Promise<string> { ... }
  async signTransaction(tx: Transaction): Promise<Transaction> { ... }
  async signAllTransactions(txs: Transaction[]): Promise<Transaction[]> { ... }
}
```

---

### ApiService — HTTP + SSE Client

```typescript
// core/services/api.service.ts
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly baseUrl = environment.apiBase; // http://localhost:3001

  get<T>(path: string, params?: Record<string, string>): Observable<T>;
  post<T>(path: string, body: unknown): Observable<T>;
  put<T>(path: string, body: unknown): Observable<T>;
  patch<T>(path: string, body: unknown): Observable<T>;
  delete<T>(path: string): Observable<T>;

  // SSE streaming for chat
  sse(path: string, body: unknown, idleTimeoutMs = 60_000): Observable<string> {
    // Uses native fetch + ReadableStream
    // Parses SSE data: lines
    // Auto-timeout if no data for idleTimeoutMs
  }
}
```

**JWT Injection:** Authorization header is automatically added (from `oprai-auth-token` in localStorage).

---

### MemoryService — Long-Term Memory

```typescript
// core/services/memory.service.ts
export interface MemoryPoint {
  id: string;
  payload: {
    summary?: string;
    type?: string;
    conversation_id?: string;
    wallet?: string;
    timestamp?: string;
  };
  score?: number;
}

export interface ConsentFlags {
  position: boolean;
  contract: boolean;
  strategy: boolean;
  preference: boolean;
  decision: boolean;
}

@Injectable({ providedIn: 'root' })
export class MemoryService {
  getMemories(): Promise<MemoryPoint[]>;
  storeMemory(payload: MemoryPoint['payload']): Promise<MemoryPoint | null>;
  searchMemories(query: string, options?: { types?: string[]; topK?: number }): Promise<MemoryPoint[]>;
  deleteMemory(id: string): Promise<boolean>;
  clearMemories(): Promise<boolean>;
  getConsent(): Promise<ConsentFlags | null>;
  updateConsent(flags: Partial<ConsentFlags>): Promise<ConsentFlags | null>;
  summarize(req: { conversation_id: string; chunk: string; token_count?: number }): Promise<SummarizeResponse | null>;
}
```

**API Endpoints:** `/memory`, `/memory/search`, `/consent`, `/summarize`

---

## Chat Feature

### ChatShellComponent

```typescript
// features/chat/pages/chat-shell/chat-shell.component.ts
@Component({
  selector: 'app-chat-shell',
  standalone: true,
  imports: [CommonModule, MessageListComponent, MessageComposerComponent],
})
export class ChatShellComponent implements OnInit, OnDestroy {
  readonly messages = signal<ChatMessage[]>([]);
  readonly streaming = signal(false);
  readonly messageActions = signal<Map<string, ParsedAction[]>>(new Map());
  readonly messageQueries = signal<Map<string, ParsedQuery[]>>(new Map());
  readonly messageClarifications = signal<Map<string, ParsedClarify[]>>(new Map());

  // Services
  private readonly chatApi = inject(ChatApiService);
  private readonly intentParser = inject(IntentParserService);
  private readonly memoryService = inject(MemoryService);
  private readonly solanaActionService = inject(SolanaActionService);
  private readonly liquidationMonitor = inject(LiquidationMonitorService);
  private readonly scheduler = inject(SchedulerService);

  // Core methods
  onSendMessage(content: string): void;
  onRetry(): void;
  clearChat(): void;
  exportChat(): void;
  onClarifySelected(action: ParsedAction): void;
  async executeActionsSequentially(actions: ParsedAction[], callbacks: ActionCallbacks): Promise<string[]>;
  sendFollowUpError(actionType: string, errorMessage: string): void;

  // Typewriter effect
  private startReveal(): void;
  private revealTick(): void;
  private finishStream(): void;
}
```

**Typewriter Effect:**
- `REVEAL_INTERVAL_MS = 8ms`
- `CHARS_PER_TICK = 6` characters
- Smooth streaming output

---

### IntentParserService — Action/Query Parsing

```typescript
// features/chat/services/intent-parser.service.ts
export interface ParsedAction {
  type: string;
  params: Record<string, string>;
  raw: string;
  chainFromPrevious?: boolean;  // Output of previous action → input of this
}

export interface ParsedQuery {
  type: string;
  params: Record<string, string>;
  raw: string;
}

export interface ParsedClarify {
  category: string;
  question: string;
  options: ClarifyOption[];
  raw: string;
}

@Injectable({ providedIn: 'root' })
export class IntentParserService {
  parseAll(content: string): ParsedIntent;
  parse(content: string): ParsedAction[];
  parseQueries(content: string): ParsedQuery[];
  parseClarifications(content: string): ParsedClarify[];
  hasActions(content: string): boolean;
}
```

**Supported Formats:**
```
[ACTION:swap] inputMint=SOL outputMint=USDC amount=1
[ACTION:swap] {"inputMint": "SOL", "outputMint": "USDC", "amount": "1"}  ← preferred
[QUERY:balance] wallet=self token=all
[CLARIFY:staking] {"question": "Which protocol?", "options": [...]}
```

**Known Action Types:**
- Core: `transfer`, `swap`, `stake`, `unstake`, `burn`, `claim`, `vote`, `launch_token`, `cross_chain_swap`, `bridge`
- Jupiter: `limit_order`, `dca`, `lend`, `borrow`, `perp_open`, `jlp_add`
- DEX: `raydium_swap`, `orca_swap`, `meteora_swap`
- NFT: `nft_buy`, `nft_list`, `me_buy`, `tensor_buy`
- Staking: `marinade_stake`, `jito_stake`
- Automation: `set_alert`, `copy_trade`, `create_schedule`, `rebalance_portfolio`

**Known Query Types:**
- `balance`, `price`, `portfolio`, `positions`, `transactions`
- `token_info`, `trending`, `analytics`, `nft_collection`
- `simulate`, `whale`, `smart_money`

---

### SolanaActionService — Transaction Execution

```typescript
// features/chat/services/solana-action.service.ts
@Injectable({ providedIn: 'root' })
export class SolanaActionService {
  async execute(action: ParsedAction, callbacks?: ActionCallbacks): Promise<string>;

  // Action handlers
  private async executeTransfer(params: Record<string, string>): Promise<string>;
  private async executeSwap(params: Record<string, string>): Promise<string>;
  private async executeStake(params: Record<string, string>): Promise<string>;
  private async executeNftBuy(params: Record<string, string>): Promise<string>;
  // ... 50+ action handlers
}
```

**Execution Flow:**
1. Parse action type + params
2. Call `/actions/quote` for swaps
3. Call `/actions/build` to get serialized TX
4. Request wallet signature
5. Call `/actions/submit` or send directly to RPC
6. Return txHash or error

---

## Portfolio Feature

### PortfolioService — Token/NFT/DeFi Aggregation

```typescript
// features/portfolio/services/portfolio.service.ts
@Injectable({ providedIn: 'root' })
export class PortfolioService {
  // State signals
  readonly summary = this._summary.asReadonly();          // PortfolioSummary
  readonly defiPositions = this._defiPositions.asReadonly();
  readonly nfts = this._nfts.asReadonly();
  readonly nftCollections = this._nftCollections.asReadonly();
  readonly enhancedTransactions = this._enhancedTransactions.asReadonly();
  readonly protocolPositions = this._protocolPositions.asReadonly();
  readonly portfolioChange = this._portfolioChange.asReadonly();

  // Loading state
  readonly loadingState = this._loadingState.asReadonly(); // 'idle' | 'loading' | 'loaded' | 'error'
  readonly activeTab = this._activeTab.asReadonly();       // 'tokens' | 'nfts' | 'defi' | 'history'

  // Core methods
  async loadPortfolio(walletAddress: string): Promise<void>;
  async loadNfts(walletAddress: string): Promise<void>;
  async loadEnhancedHistory(walletAddress: string): Promise<void>;
  async loadMoreHistory(walletAddress: string): Promise<void>;
  setActiveTab(tab: PortfolioTab, walletAddress: string | null): void;
  async refresh(walletAddress: string): Promise<void>;
  reset(): void;
}
```

**PortfolioSummary:**
```typescript
interface PortfolioSummary {
  walletAddress: string;
  solBalance: {
    lamports: number;
    sol: number;
    usdPrice: number | null;
    usdValue: number | null;
    priceChange24h: number | null;
    allocationPercent: number;
  };
  tokens: EnhancedTokenAccount[];
  totalUsdValue: number;
}
```

**Data Sources:**
- **SOL Balance:** Solana RPC (`getBalance`)
- **Token Accounts:** Solana RPC (`getTokenAccountsByOwner`)
- **Stake Accounts:** Solana RPC (`getStakeAccounts`)
- **Prices + 24h Change:** Birdeye API
- **Token Metadata:** Jupiter Strict List → DexScreener fallback
- **NFT Assets:** Helius DAS API (`getAssetsByOwner`)
- **Transaction History:** Helius Enhanced Transactions API
- **DeFi Positions:** Protocol-specific parsers (Marinade, Jito, Raydium, etc.)

---

### PortfolioShellComponent

```typescript
// features/portfolio/pages/portfolio-shell/portfolio-shell.component.ts
@Component({
  selector: 'app-portfolio-shell',
  standalone: true,
  imports: [
    CommonModule, LucideAngularModule,
    PortfolioOverviewComponent, PortfolioTabsComponent,
    TokenListComponent, DefiPositionsComponent,
    NftGalleryComponent, RecentActivityComponent,
  ],
})
export class PortfolioShellComponent implements OnDestroy {
  readonly benchmarkQuotes = signal<HeaderMetric[]>([...]); // SOL, BTC, ETH prices
  readonly connected = this.walletService.connected;
  readonly summary = this.portfolioService.summary;
  readonly activeTab = this.portfolioService.activeTab;

  onTabChange(tab: PortfolioTab): void;
  refresh(): void;
  loadMoreHistory(): void;
}
```

---

## Design System

### CSS Tokens

```scss
// styles.scss — Design Tokens
:root {
  // Surfaces
  --op-bg-base: #f8f9fa;
  --op-bg-surface-1: #ffffff;
  --op-bg-surface-2: #f1f3f5;

  // Text
  --op-text-primary: #1a1b1e;
  --op-text-secondary: #495057;
  --op-text-tertiary: #868e96;

  // Brand: Indigo → Cyan
  --op-brand: #5b5fc7;
  --op-brand-hover: #4a4eb5;
  --op-accent: #06B6D4;
  --op-gradient: linear-gradient(135deg, #5b5fc7, #06B6D4);

  // Semantic
  --op-gain: #16a34a;
  --op-loss: #dc2626;
  --op-warning: #d97706;
  --op-info: #2563eb;

  // Borders
  --op-border-subtle: rgba(0, 0, 0, 0.06);
  --op-border-default: rgba(0, 0, 0, 0.10);

  // Typography
  --op-font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --op-font-display: 'Sora', 'Inter', sans-serif;
  --op-font-mono: 'JetBrains Mono', monospace;

  // Spacing
  --op-space-1: 4px;
  --op-space-2: 8px;
  --op-space-3: 12px;
  --op-space-4: 16px;
  --op-space-6: 24px;
  --op-space-8: 32px;

  // Radii
  --op-radius-sm: 6px;
  --op-radius-md: 10px;
  --op-radius-lg: 14px;
  --op-radius-xl: 20px;

  // Shadows
  --op-shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.06);
  --op-shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.07);
  --op-shadow-card: 0 1px 3px rgba(0, 0, 0, 0.04), 0 0 0 1px var(--op-border-subtle);

  // Transitions
  --op-fast: 150ms cubic-bezier(0.4, 0, 0.2, 1);
  --op-normal: 250ms cubic-bezier(0.4, 0, 0.2, 1);

  // Layout
  --op-header-h: 52px;
  --op-sidebar-w: 260px;
}
```

### Dark Mode

```scss
@media (prefers-color-scheme: dark) {
  :root {
    --op-bg-base: #0f0f12;
    --op-bg-surface-1: #18181b;
    --op-bg-surface-2: #1f1f23;
    --op-text-primary: #fafafa;
    --op-text-secondary: #a1a1aa;
    --op-border-subtle: rgba(255, 255, 255, 0.06);
    // ...
  }
}
```

---

## Component Patterns

### Standalone Components

```typescript
@Component({
  selector: 'app-chat-shell',
  standalone: true,
  imports: [
    CommonModule,
    MessageListComponent,
    MessageComposerComponent,
  ],
  templateUrl: './chat-shell.component.html',
  styleUrl: './chat-shell.component.scss',
})
export class ChatShellComponent { ... }
```

### Signals for State

```typescript
// Modern Angular signals pattern
readonly messages = signal<ChatMessage[]>([]);
readonly streaming = signal(false);

// Update
this.messages.update(msgs => [...msgs, newMessage]);

// Computed
readonly isLoading = computed(() => this._loadingState() === 'loading');
```

### Effect for Side Effects

```typescript
private readonly walletEffect = effect(() => {
  const key = this.publicKey();
  if (key) {
    this.portfolioService.loadPortfolio(key);
  } else {
    this.portfolioService.reset();
  }
});
```

---

## API Endpoints Used

| Feature | Endpoint | Method | Description |
|---------|----------|--------|-------------|
| Auth | `/auth/nonce` | POST | Get nonce for signing |
| Auth | `/auth/verify` | POST | Verify signature → JWT |
| Chat | `/chat/sessions/{id}/messages/stream` | POST (SSE) | Streaming chat |
| Chat | `/chat/sessions` | GET | List sessions |
| Portfolio | `/balance/` | GET | Token balances |
| Market | `/market/prices` | GET | Token prices |
| Market | `/market/tokens/{mint}` | GET | Token metadata |
| Memory | `/memory` | GET/POST | Memory CRUD |
| Memory | `/memory/search` | GET | Semantic search |
| Memory | `/summarize` | POST | Summarize conversation |
| Actions | `/actions/quote` | POST | Swap quote |
| Actions | `/actions/build` | POST | Build transaction |
| Actions | `/actions/submit` | POST | Submit transaction |

---

## Build & Run

```bash
# Development
cd apps/oprai
npx ng serve --port 3000

# Build
npx ng build --configuration production

# Test
npx ng test                    # Karma + Jasmine
npx ng test --include='**/chat/**'  # Specific feature

# Lint
npx ng lint
```

---

## Testing

```bash
# Run all tests
npx ng test --no-watch --browsers=ChromeHeadless

# Run specific test file
npx ng test --include='**/intent-parser.service.spec.ts'

# Coverage
npx ng test --no-watch --code-coverage
```

**Test Example:**
```typescript
// intent-parser.service.spec.ts
describe('IntentParserService', () => {
  it('should parse JSON action blocks', () => {
    const content = '[ACTION:swap] {"inputMint": "SOL", "amount": "1"}';
    const actions = service.parse(content);
    expect(actions[0].type).toBe('swap');
    expect(actions[0].params['inputMint']).toBe('SOL');
  });
});
```

---

## Dependencies

```json
{
  "@angular/core": "^19.0.0",
  "@angular/common": "^19.0.0",
  "@angular/router": "^19.0.0",
  "@angular/forms": "^19.0.0",
  "@solana/web3.js": "^1.95.0",
  "@solana/wallet-adapter-angular": "^0.3.0",
  "@solana/spl-token": "^0.4.0",
  "lucide-angular": "^0.400.0",
  "rxjs": "^7.8.0",
  "zone.js": "^0.15.0"
}
```
