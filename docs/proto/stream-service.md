# proto/stream/stream.proto

Real-time streaming services. WebSocket / gRPC streaming based.

## Service: StreamService

Bidirectional streaming service for real-time data flow.

### RPC Methods (All streaming)

| Method | Request | Response (stream) | Description |
|--------|---------|-------------------|-------------|
| `SubscribePrices` | PriceSubscribeRequest | `stream` PriceUpdate | Price updates |
| `SubscribePositions` | PositionSubscribeRequest | `stream` PositionUpdate | Portfolio updates |
| `SubscribeNotifications` | NotificationSubscribeRequest | `stream` Notification | Notifications |
| `SubscribeTransactions` | TxSubscribeRequest | `stream` TxStatusUpdate | TX status |
| `ChatStream` | `stream` ChatMessage | `stream` ChatMessage | Bidirectional chat |
| `SubscribeMarketEvents` | MarketEventSubscribeRequest | `stream` MarketEvent | Market events |

---

## Price Streaming

### PriceSubscribeRequest

| Field | Type | Description |
|-------|------|-------------|
| `token_addresses` | `repeated string` | Token mint addresses |
| `interval_ms` | `int64` | Update interval (ms, default: 1000) |

### PriceUpdate

| Field | Type | Description |
|-------|------|-------------|
| `token_address` | `string` | Token mint |
| `price` | `double` | Current price (USD) |
| `price_change_24h` | `double` | 24h change (%) |
| `volume_24h` | `double` | 24h volume |
| `timestamp` | `int64` | Timestamp |
| `prices` | `repeated PriceUpdate` | Batch update |

**Example Flow:**
```
Client: SubscribePrices({ token_addresses: ["SOL_MINT", "USDC_MINT"], interval_ms: 2000 })

Server -> Frame 1 (2s later):
{
  "prices": [
    { "token_address": "SOL_MINT", "price": 150.42, "price_change_24h": 2.5, "volume_24h": 1000000 },
    { "token_address": "USDC_MINT", "price": 1.0, "price_change_24h": 0.01, "volume_24h": 5000000 }
  ],
  "timestamp": 1704067220
}

Server -> Frame 2 (4s later):
...
```

---

## Position Streaming

### PositionSubscribeRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet_address` | `string` | Wallet to monitor |
| `protocols` | `repeated string` | Protocol filter (optional) |

### PositionUpdate

| Field | Type | Description |
|-------|------|-------------|
| `wallet_address` | `string` | Wallet |
| `protocol` | `string` | Protocol (marinade, jito, etc.) |
| `token_address` | `string` | Token mint |
| `token_symbol` | `string` | Token symbol |
| `amount` | `double` | Amount |
| `value_usd` | `double` | USD value |
| `apy` | `double` | APY (if applicable) |
| `change` | `PositionChange` | Change type |
| `timestamp` | `int64` | Time |

### PositionChange (Enum)

| Value | Number | Description |
|-------|--------|-------------|
| `CHANGE_UNSPECIFIED` | 0 | Unspecified |
| `CHANGE_DEPOSIT` | 1 | Deposit |
| `CHANGE_WITHDRAW` | 2 | Withdrawal |
| `CHANGE_PROFIT` | 3 | Profit |
| `CHANGE_LOSS` | 4 | Loss |
| `CHANGE_REWARD` | 5 | Reward |

---

## Notification Streaming

### NotificationSubscribeRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet_address` | `string` | Wallet |
| `types` | `repeated NotificationType` | Filter (optional) |

### NotificationType (Enum)

| Value | Number | Description |
|-------|--------|-------------|
| `TYPE_UNSPECIFIED` | 0 | Unspecified |
| `TYPE_PRICE_ALERT` | 1 | Price alert |
| `TYPE_POSITION_ALERT` | 2 | Position alert |
| `TYPE_TRANSACTION` | 3 | Transaction notification |
| `TYPE_AIRDROP` | 4 | Airdrop |
| `TYPE_SYSTEM` | 5 | System message |
| `TYPE_WHALE` | 6 | Whale activity |

### Notification

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | UUID |
| `type` | `NotificationType` | Type |
| `title` | `string` | Title |
| `body` | `string` | Content |
| `data` | `string` | JSON extra data |
| `timestamp` | `int64` | Time |
| `priority` | `Priority` | Priority |
| `read` | `bool` | Has been read? |

### Priority (Enum)

| Value | Number | Description |
|-------|--------|-------------|
| `PRIORITY_UNSPECIFIED` | 0 | Unspecified |
| `PRIORITY_LOW` | 1 | Low |
| `PRIORITY_NORMAL` | 2 | Normal |
| `PRIORITY_HIGH` | 3 | High |
| `PRIORITY_URGENT` | 4 | Urgent |

**Example Notification:**
```json
{
  "id": "notif_abc123",
  "type": "TYPE_PRICE_ALERT",
  "title": "SOL Price Alert",
  "body": "SOL has reached your target price of $160",
  "data": "{\"token\":\"SOL\",\"target_price\":160,\"current_price\":160.5}",
  "timestamp": 1704067200,
  "priority": "PRIORITY_HIGH",
  "read": false
}
```

---

## Transaction Status Streaming

### TxSubscribeRequest

| Field | Type | Description |
|-------|------|-------------|
| `transaction_signatures` | `repeated string` | TX signatures |

### TxStatusUpdate

| Field | Type | Description |
|-------|------|-------------|
| `signature` | `string` | TX signature |
| `status` | `TxStatus` | Status |
| `block_time` | `int64` | Block time |
| `confirmations` | `int64` | Confirmation count |
| `error_message` | `string` | Error message |

### TxStatus (Enum)

| Value | Number | Description |
|-------|--------|-------------|
| `STATUS_UNSPECIFIED` | 0 | Unspecified |
| `STATUS_PENDING` | 1 | Pending |
| `STATUS_CONFIRMED` | 2 | Confirmed |
| `STATUS_FAILED` | 3 | Failed |
| `STATUS_PROCESSED` | 4 | Processed (saved to DB) |

**Flow:**
```
1. User submits TX
   Frontend: SubscribeTransactions([signature])

2. Server streams:
   -> { status: PENDING, confirmations: 0 }
   -> { status: CONFIRMED, confirmations: 1 }
   -> { status: CONFIRMED, confirmations: 5 }
   -> { status: CONFIRMED, confirmations: 32 }
   -> { status: PROCSED, block_time: 1704067200 }
```

---

## Chat Streaming (Bidirectional)

### ChatMessage

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Message ID |
| `session_id` | `string` | Chat session |
| `wallet_address` | `string` | Wallet |
| `content` | `string` | Message content |
| `type` | `MessageType` | Message type |
| `timestamp` | `int64` | Time |
| `metadata` | `map<string, string>` | Extra data |

### MessageType (Enum)

| Value | Number | Description |
|-------|--------|-------------|
| `MESSAGE_TYPE_UNSPECIFIED` | 0 | Unspecified |
| `MESSAGE_TYPE_USER` | 1 | User message |
| `MESSAGE_TYPE_ASSISTANT` | 2 | AI response |
| `MESSAGE_TYPE_SYSTEM` | 3 | System message |
| `MESSAGE_TYPE_ACTION` | 4 | Action block |

**Bidirectional Flow:**
```
Client -> Server:
{
  "id": "msg_1",
  "session_id": "sess_abc",
  "wallet_address": "Hx7b...",
  "content": "Swap 1 SOL to USDC",
  "type": "MESSAGE_TYPE_USER"
}

Server -> Client (streaming):
{ "type": "MESSAGE_TYPE_ASSISTANT", "content": "1", "metadata": {"partial": "true"} }
{ "type": "MESSAGE_TYPE_ASSISTANT", "content": " SOL", "metadata": {"partial": "true"} }
{ "type": "MESSAGE_TYPE_ASSISTANT", "content": " =", "metadata": {"partial": "true"} }
{ "type": "MESSAGE_TYPE_ASSISTANT", "content": " 150", "metadata": {"partial": "true"} }
{ "type": "MESSAGE_TYPE_ASSISTANT", "content": ".42 USDC", "metadata": {"partial": "true"} }
{ "type": "MESSAGE_TYPE_ACTION", "content": "[ACTION:swap]...", "metadata": {"complete": "true"} }
```

---

## Market Events Streaming

### MarketEventSubscribeRequest

| Field | Type | Description |
|-------|------|-------------|
| `token_addresses` | `repeated string` | Token mints |
| `event_types` | `repeated MarketEventType` | Event types |

### MarketEventType (Enum)

| Value | Number | Description |
|-------|--------|-------------|
| `EVENT_TYPE_UNSPECIFIED` | 0 | Unspecified |
| `EVENT_TYPE_TRADE` | 1 | Trade |
| `EVENT_TYPE_LIQUIDITY_CHANGE` | 2 | LP change |
| `EVENT_TYPE_NEW_POOL` | 3 | New pool |
| `EVENT_TYPE_BIG_TRADE` | 4 | Large trade (whale) |

### MarketEvent

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Event ID |
| `type` | `MarketEventType` | Type |
| `token_address` | `string` | Token |
| `pool_address` | `string` | Pool |
| `price` | `double` | Price |
| `amount` | `double` | Amount |
| `from_address` | `string` | Sender |
| `to_address` | `string` | Recipient |
| `timestamp` | `int64` | Time |

**Whale Alert Example:**
```json
{
  "id": "evt_whale_1",
  "type": "EVENT_TYPE_BIG_TRADE",
  "token_address": "SOL_MINT",
  "pool_address": "ORCA_POOL",
  "price": 150.42,
  "amount": 50000,
  "from_address": "Hx7b...",
  "to_address": "9WzD...",
  "timestamp": 1704067200
}
```
-> This is a $50,000 SOL trade - may trigger a whale alert

---

## Health Check

### StreamHealthCheckRequest / Response

| Response Field | Type | Description |
|----------------|------|-------------|
| `healthy` | `bool` | Is healthy? |
| `active_streams` | `int64` | Active stream count |
| `messages_per_second` | `int64` | Messages per second |

---

## Backend Implementation (Python)
```python
# services/chat-service-py/app/grpc/stream.py
import asyncio
from typing import AsyncIterator
import grpc
from openai import AsyncOpenAI

class StreamServicer(stream_pb2_grpc.StreamServiceServicer):
    def __init__(self):
        self.openai = AsyncOpenAI()
        self.active_streams = 0
        self.message_counter = 0

    async def ChatStream(
        self,
        request_iterator: AsyncIterator[stream_pb2.ChatMessage],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[stream_pb2.ChatMessage]:
        self.active_streams += 1
        try:
            async for user_message in request_iterator:
                # Stream LLM response
                stream = await self.openai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": user_message.content}],
                    stream=True
                )

                async for chunk in stream:
                    if chunk.choices[0].delta.content:
                        self.message_counter += 1
                        yield stream_pb2.ChatMessage(
                            id=str(uuid.uuid4()),
                            session_id=user_message.session_id,
                            wallet_address=user_message.wallet_address,
                            content=chunk.choices[0].delta.content,
                            type=stream_pb2.ChatMessage.MESSAGE_TYPE_ASSISTANT,
                            timestamp=int(time.time()),
                            metadata={"partial": "true"}
                        )

                # Final message
                yield stream_pb2.ChatMessage(
                    id=str(uuid.uuid4()),
                    session_id=user_message.session_id,
                    wallet_address=user_message.wallet_address,
                    content="",
                    type=stream_pb2.ChatMessage.MESSAGE_TYPE_ACTION,
                    metadata={"complete": "true"}
                )
        finally:
            self.active_streams -= 1
```

---

## Frontend Implementation (Angular)
```typescript
// apps/oprai/src/app/services/stream.service.ts
@Injectable({ providedIn: 'root' })
export class StreamService {
    private grpcClient: StreamServiceClient;

    constructor() {
        this.grpcClient = new StreamServiceClient(
            'wss://api.oprai.io/stream',
            credentials.createInsecure()
        );
    }

    // Chat streaming
    chatStream(): Observable<ChatMessage> {
        return new Observable(observer => {
            const call = this.grpcClient.chatStream();

            call.on('data', (message: ChatMessage) => {
                observer.next(message);
            });

            call.on('end', () => {
                observer.complete();
            });

            // Send user message
            return () => {
                call.end();
            };
        });
    }

    // Price streaming
    subscribePrices(tokens: string[], intervalMs: number = 1000): Observable<PriceUpdate> {
        return new Observable(observer => {
            const call = this.grpcClient.subscribePrices({
                token_addresses: tokens,
                interval_ms: intervalMs
            });

            call.on('data', (update: PriceUpdate) => {
                observer.next(update);
            });

            return () => call.cancel();
        });
    }

    // Transaction status
    subscribeTransactions(signatures: string[]): Observable<TxStatusUpdate> {
        return new Observable(observer => {
            const call = this.grpcClient.subscribeTransactions({
                transaction_signatures: signatures
            });

            call.on('data', (update: TxStatusUpdate) => {
                observer.next(update);
                if (update.status === 'STATUS_PROCESSED') {
                    observer.complete();
                    call.cancel();
                }
            });

            return () => call.cancel();
        });
    }
}
```

---

## WebSocket vs gRPC Streaming

| Feature | WebSocket | gRPC Streaming |
|---------|-----------|----------------|
| Protocol | HTTP/1.1 | HTTP/2 |
| Typing | Manual | Proto-based |
| Bidirectional | Yes | Yes |
| Flow Control | Manual | Built-in |
| Compression | Manual | gzip |
| Browser Support | Native | grpc-web required |

**In OPRAI:** grpc-web is used (for the frontend).

---

## Scalability

```
                    +-----------------+
                    |   Load Balancer  |
                    +--------+--------+
                             |
         +-------------------+-------------------+
         |                   |                   |
         v                   v                   v
    +---------+        +---------+        +---------+
    | Stream  |        | Stream  |        | Stream  |
    | Pod 1   |        | Pod 2   |        | Pod 3   |
    +----+----+        +----+----+        +----+----+
         |                  |                  |
         +------------------+------------------+
                            |
                    +-------+-------+
                    |    Redis      |
                    |   Pub/Sub     |
                    +---------------+
```

**Redis Pub/Sub:** For communication between users connected to different pods.
