# chat-service eval

Behavioural regression suite. 40 canonical questions across category
listings, swap / transfer / stake actions, balance / price queries,
SNS-domain analyse-this, compares, ambiguous prompts, and chitchat.

Each row in `qa_set.jsonl` carries the expected outcome shape:
- `must_contain` — substrings that must appear in the streamed text
- `must_not_contain` — refusal phrases ("Maalesef", "I cannot access", …)
- `must_have_action` — an action card with that `action_type`
- `must_contain_params` — params that must be present on the action
- `must_have_action_or_clarify` — at least one card emitted
- `must_not_have_action` — chitchat shouldn't fire actions
- `max_length` — short answers stay short

## Running locally

```bash
cd services/chat-service-py
.venv/bin/python -m eval.run_eval                   # full run
.venv/bin/python -m eval.run_eval --ids cat-stable-tr,swap-basic-en
.venv/bin/python -m eval.run_eval --json out.json   # write summary
.venv/bin/python -m eval.run_eval --gate 0.85       # exit 1 if <85%
```

Requires:
- `DATABASE_URL` reachable (uses the dev Postgres at `:5433` by default —
  rows write to `chat_schema` via fresh `eval_<uuid>` session IDs and are
  not cleaned up; harmless but visible).
- `OPRAI_OPENAI_API_KEY` (or `OPRAI_ANTHROPIC_API_KEY` if provider is
  switched).

## CI

`.github/workflows/python-services.yml` runs `--gate 0.85` on chat-service
PRs. Below that threshold the build fails and the PR is blocked.

## Adding a row

Append one JSON line to `qa_set.jsonl`. Pick a unique `id`, list the
substrings the model SHOULD and SHOULDN'T emit, and the card shape (if
any) expected. Start with `must_not_contain` for failure phrases the
model has historically produced for that question — those catch
regressions reliably.
