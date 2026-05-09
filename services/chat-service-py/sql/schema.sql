-- Chat Service Schema DDL
-- Idempotent: uses IF NOT EXISTS.
-- Run via: make migrate  (or psql -f this file)

CREATE SCHEMA IF NOT EXISTS chat_schema;
