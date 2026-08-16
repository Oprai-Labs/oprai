-- FAZ-2 analytics views (read-only aggregations over the FAZ-1 capture data).
--
-- Panel-agnostic: any consumer (future admin panel, admin-service, ad-hoc SQL)
-- reads admin_schema.v_*. Owned by admin_app, which already has cross-schema
-- SELECT on auth/chat/solana (+ default privileges pick up the new capture
-- tables); analytics_schema is granted below. Views never touch the capture
-- path — pure reads. Idempotent (CREATE OR REPLACE), safe to re-apply.

-- admin_app is the analytics reader; let it read the events schema too.
GRANT USAGE ON SCHEMA analytics_schema TO admin_app;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics_schema TO admin_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics_schema GRANT SELECT ON TABLES TO admin_app;

-- Create the views AS admin_app so it owns them and can CREATE OR REPLACE later.
SET ROLE admin_app;

-- ── #5 Engagement ────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW admin_schema.v_user_engagement AS
SELECT
    u.wallet_address,
    u.created_at                              AS first_seen,
    u.last_active_at,
    COALESCE(s.sessions, 0)                   AS sessions,
    COALESCE(m.messages, 0)                   AS messages,
    CASE WHEN COALESCE(s.sessions, 0) > 0
         THEN round(COALESCE(m.messages, 0)::numeric / s.sessions, 2)
         ELSE 0 END                           AS avg_messages_per_session,
    COALESCE(a.active_days, 0)                AS active_days,
    EXTRACT(DAY FROM now() - u.last_active_at)::int AS days_since_active
FROM auth_schema.users u
LEFT JOIN (SELECT wallet_address, count(*) sessions
           FROM chat_schema.chat_sessions WHERE is_deleted = false GROUP BY 1) s
       ON s.wallet_address = u.wallet_address
LEFT JOIN (SELECT wallet_address, count(*) messages
           FROM chat_schema.chat_messages GROUP BY 1) m
       ON m.wallet_address = u.wallet_address
LEFT JOIN (SELECT wallet_address, count(DISTINCT date_trunc('day', created_at)) active_days
           FROM chat_schema.chat_messages GROUP BY 1) a
       ON a.wallet_address = u.wallet_address
WHERE u.is_deleted = false;

-- DAU / WAU / MAU snapshot (successful logins).
CREATE OR REPLACE VIEW admin_schema.v_active_users AS
SELECT
    count(DISTINCT wallet_address) FILTER (WHERE created_at >= now() - interval '1 day')   AS dau,
    count(DISTINCT wallet_address) FILTER (WHERE created_at >= now() - interval '7 days')   AS wau,
    count(DISTINCT wallet_address) FILTER (WHERE created_at >= now() - interval '30 days')  AS mau
FROM auth_schema.login_logs
WHERE success = true;

-- Daily active users + new signups, last 90 days.
CREATE OR REPLACE VIEW admin_schema.v_engagement_daily AS
SELECT
    d::date AS day,
    (SELECT count(DISTINCT wallet_address) FROM auth_schema.login_logs
     WHERE success AND created_at::date = d::date)                          AS active_users,
    (SELECT count(*) FROM auth_schema.users
     WHERE created_at::date = d::date AND is_deleted = false)               AS new_users
FROM generate_series(now()::date - 89, now()::date, interval '1 day') d;

-- ── #6 Rankings & profitability ──────────────────────────────────────────
-- Per-wallet economics: what they traded, what they paid us, what they cost us.
CREATE OR REPLACE VIEW admin_schema.v_user_economics AS
SELECT
    COALESCE(r.user_wallet, l.wallet)          AS wallet,
    COALESCE(r.lifetime_notional_usd, 0)       AS volume_usd,
    COALESCE(r.lifetime_fee_usd, 0)            AS fee_revenue_usd,
    COALESCE(l.llm_cost_usd, 0)                AS llm_cost_usd,
    COALESCE(r.lifetime_fee_usd, 0) - COALESCE(l.llm_cost_usd, 0) AS profit_usd,
    COALESCE(r.confirmed_tx_count, 0)          AS tx_count
FROM solana_schema.wallet_economics_rollup r
FULL OUTER JOIN (SELECT wallet, sum(cost_usd) llm_cost_usd
                 FROM chat_schema.llm_usage GROUP BY 1) l
  ON l.wallet = r.user_wallet;

-- Revenue and volume grouped by protocol.
CREATE OR REPLACE VIEW admin_schema.v_revenue_by_protocol AS
SELECT
    COALESCE(protocol, 'unknown') AS protocol,
    sum(volume_usd)               AS volume_usd,
    sum(fee_usd)                  AS fee_usd,
    sum(tx_count)                 AS tx_count
FROM solana_schema.daily_economics_rollup
GROUP BY 1
ORDER BY fee_usd DESC;

-- ── #7 Trends ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW admin_schema.v_daily_revenue AS
SELECT stat_date AS day, sum(volume_usd) AS volume_usd, sum(fee_usd) AS fee_usd, sum(tx_count) AS tx_count
FROM solana_schema.daily_economics_rollup
GROUP BY 1 ORDER BY 1 DESC;

CREATE OR REPLACE VIEW admin_schema.v_daily_llm_cost AS
SELECT stat_date AS day,
       sum(cost_usd)                          AS cost_usd,
       sum(prompt_tokens + completion_tokens) AS tokens,
       sum(requests)                          AS requests
FROM chat_schema.llm_usage_daily
GROUP BY 1 ORDER BY 1 DESC;

-- ── #8 Anomaly ───────────────────────────────────────────────────────────
-- Wallets whose last-24h LLM cost is >=5x their trailing-30d daily average
-- (and material in absolute terms) — abuse / runaway-cost signal.
CREATE OR REPLACE VIEW admin_schema.v_anomaly_llm_cost AS
WITH recent AS (
    SELECT wallet, sum(cost_usd) AS cost_24h
    FROM chat_schema.llm_usage WHERE created_at >= now() - interval '1 day' GROUP BY 1
), base AS (
    SELECT wallet, sum(cost_usd) / 30.0 AS avg_daily_30d
    FROM chat_schema.llm_usage WHERE created_at >= now() - interval '30 days' GROUP BY 1
)
SELECT
    r.wallet,
    round(r.cost_24h, 4)                              AS cost_24h,
    round(b.avg_daily_30d, 4)                         AS avg_daily_30d,
    round(r.cost_24h / NULLIF(b.avg_daily_30d, 0), 1) AS x_over_avg
FROM recent r JOIN base b ON b.wallet = r.wallet
WHERE r.cost_24h >= 5 * NULLIF(b.avg_daily_30d, 0) AND r.cost_24h > 0.50
ORDER BY x_over_avg DESC;

-- ── FAZ-3: tier + points (computed from volume + referrals) ──────────────
-- Current tier per wallet = highest tier whose min_volume_usd the lifetime
-- volume clears. Wallets with no volume aren't listed → callers default to T1.
CREATE OR REPLACE VIEW admin_schema.v_user_tier AS
SELECT
    r.user_wallet AS wallet,
    r.lifetime_notional_usd AS volume_usd,
    (SELECT max(c.tier) FROM analytics_schema.tier_config c
     WHERE r.lifetime_notional_usd >= c.min_volume_usd) AS tier
FROM solana_schema.wallet_economics_rollup r;

-- Points = own volume (1 pt / $1) + 30% of each referee's own points.
-- Referral points scale with the REFERRER's current tier (20% Bronze -> 50% Legend),
-- mirroring the frontend tier ladder. Percentages live here + in the Angular TIERS
-- array; when the fee-discount work lands they move to a tier_config column (SoT).
CREATE OR REPLACE VIEW admin_schema.v_user_points AS
WITH own AS (
    SELECT user_wallet AS wallet, floor(lifetime_notional_usd)::bigint AS own_points
    FROM solana_schema.wallet_economics_rollup
), referrer_tier AS (
    SELECT r.user_wallet AS wallet,
           (SELECT max(c.tier) FROM analytics_schema.tier_config c
            WHERE r.lifetime_notional_usd >= c.min_volume_usd) AS tier
    FROM solana_schema.wallet_economics_rollup r
), refpts AS (
    SELECT rf.referrer_wallet AS wallet,
           floor(sum(o.own_points) *
                 CASE COALESCE(rt.tier, 1)
                     WHEN 1 THEN 0.20 WHEN 2 THEN 0.25 WHEN 3 THEN 0.30
                     WHEN 4 THEN 0.35 WHEN 5 THEN 0.40 WHEN 6 THEN 0.50
                     ELSE 0.20 END
           )::bigint AS referral_points
    FROM analytics_schema.referrals rf
    JOIN own o ON o.wallet = rf.referee_wallet
    LEFT JOIN referrer_tier rt ON rt.wallet = rf.referrer_wallet
    GROUP BY rf.referrer_wallet, rt.tier
)
SELECT
    COALESCE(o.wallet, r.wallet)                                        AS wallet,
    COALESCE(o.own_points, 0)                                          AS own_points,
    COALESCE(r.referral_points, 0)                                     AS referral_points,
    COALESCE(o.own_points, 0) + COALESCE(r.referral_points, 0)         AS total_points
FROM own o
FULL OUTER JOIN refpts r ON r.wallet = o.wallet;

RESET ROLE;

-- chat-service (chat_app) serves the user-facing rewards endpoint; let it read
-- these two aggregate views only (they run as admin_app, so no raw cross-schema
-- access is granted to chat_app).
GRANT USAGE ON SCHEMA admin_schema TO chat_app;  -- reference the two views below (SELECT is per-view, so only these are readable)
GRANT SELECT ON admin_schema.v_user_tier  TO chat_app;
GRANT SELECT ON admin_schema.v_user_points TO chat_app;
