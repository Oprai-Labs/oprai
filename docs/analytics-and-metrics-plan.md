# OPRAI — Analytics & Metrics Data-Capture Plan

> Canlı tasarım dokümanı. Her madde konuşula konuşula kilitlenir. Sıra: önce veri
> yakalama temeli (ileriye dönük birikir), sonra türetilmiş analitik, sonra yeni
> özellikler. Geçmiş veri kapsam dışı — bundan sonrası önemli.

**Durum:** 🔒 kilitli · ✏️ tasarımda · ⬜ sırada
**Yakalama durumu:** ✅ veri var · 🟡 kısmi · ❌ yeni yakalama

---

## Master Plan

### FAZ 1 — Veri yakalama temeli
1. ✏️ **Fee/Gelir kaydı** — onaylı tx başına OPRAI komisyonu
2. ✏️ **İşlem hacmi** — sayısal (token + USD), fee ile aynı tabloda
3. ⬜ **LLM token + $ maliyet persistence + kullanıcı kullanım limiti**
4. ⬜ **Ürün-analitiği event tablosu** — kullanıcı × app etkileşimi, funnel

### FAZ 2 — Türetilmiş analitik
5. ⬜ Engagement (oturum başına ort. mesaj/süre, aktif gün, DAU/WAU/MAU)
6. ⬜ Sıralamalar & kesitler (en pahalı kullanıcılar, en çok fee ödeyenler, en çok hacim + neyle etkileşmiş, protokol bazlı gelir)
7. ⬜ Gelir/fee trendleri
8. ⬜ Anomali tespiti

### FAZ 3 — Yeni özellikler
9. ⬜ Puan sistemi
10. ⬜ Referral sistemi

### Güvenlik (ayrı izlenen)
- ⬜ **Fee-bütünlüğü / anti-bypass** — kullanıcı komisyonu çıkarılmış kendi tx'ini imzalayıp gönderemesin; "beklenen fee vs zincire giden fee" tespiti. (WYSIWYS/tx-güven modeliyle bağlantılı.)

---

## #1+#2 — Per-tx Ekonomi Defteri (fee + hacim)  ✏️

**Karar:** fee ve hacim ikisi de "onaylı tx başına" olduğu için **tek tablo**.
**Sahip:** solana-service (fee'yi hesaplayan + `transactions`'ı yazan). **Yer:** `solana_schema` (admin cross-schema okur). İleride analitik büyürse ayrı `analytics_schema`/servise taşınır.

**Tablo:** `solana_schema.tx_economics` (öneri)
| Alan | Açıklama |
|---|---|
| `tx_signature` (unique) | zincir imzası (→ transactions ile ilişki) |
| `user_wallet` | kullanıcı |
| `protocol`, `action` | Jupiter/Kamino/… · swap/stake/lend/launch/… |
| `input_mint`, `input_amount` | hacim: giren |
| `output_mint`, `output_amount` | hacim: çıkan |
| `notional_usd` | tx'in USD notional'ı (best-effort snapshot) |
| `fee_mint`, `fee_amount_token` | komisyon (token — **ana kaynak**) |
| `token_price_usd`, `fee_usd` | komisyonun USD snapshot'ı (ikincil) |
| `status`, `confirmed_at`, `created_at` | sadece confirmed sayılır |

**Kilitlenen kararlar (#1):** token miktarı ana kaynak + USD snapshot ikincil ✅ · confirmed anında kayıt ✅ · ileriye dönük ✅ · solana_schema ✅

**#2 açık sorular:** (aşağıda konuşuluyor)
