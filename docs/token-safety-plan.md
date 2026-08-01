# Token safety

## The standard

A user with no DeFi knowledge and no security knowledge must not be able to
lose money to a token whose danger was detectable, without having been stopped
and told why in plain language.

That is the bar. "100% safe" is not — a token can be clean at signing time and
rugged an hour later, liquidity can be pulled, a team can walk away, and no
check catches a project that simply fails. Promising more than we can detect
is itself a safety problem, because it teaches people to stop looking.

What follows is what we can actually detect, where it is enforced, and what we
cannot.

## Principle 1 — the check runs at signing, not on request

A user who knows to ask "is this a scam?" is not the user at risk. The one at
risk types "buy 1 SOL of BONKKILLER" and clicks Confirm.

So the analysis runs automatically on every action that acquires or spends an
unfamiliar token, and its result is on the card before Confirm is reachable.
Asking is the fallback path, not the primary one.

## Principle 2 — ground truth comes from the mint account

Verified 2026-08-01: a token minted minutes earlier returned three populated
fields from Birdeye and complete authority data from `getAccountInfo`. Scams
live in exactly that window — the first hour, before any indexer has caught
up. An indexer that is blind when it matters is not a safety layer.

So: the mint account is the source of truth for the facts that decide whether
money can be taken. Indexers enrich; they do not decide.

  from the mint account (always available, cannot be stale)
    mintAuthority        supply can still be inflated
    freezeAuthority      your account can be frozen — you cannot sell
    Token-2022 extensions
      TransferFeeConfig  the tax, with its actual percentage
      PermanentDelegate  someone else can move your tokens at will
      NonTransferable    it cannot be sold at all
      DefaultAccountState  new accounts start frozen

  from Birdeye (enrichment)
    top10HolderPercent, creatorPercentage, lockInfo, mutableMetadata

  from Jupiter Shield (enrichment)
    NOT_VERIFIED, LOW_LIQUIDITY, LOW_ORGANIC_ACTIVITY, NEW_LISTING

## Principle 3 — warn about what can take the money, not about what is new

Every pump.fun token is unverified, new, and thinly traded. If those raise an
alarm, every memecoin raises an alarm, and users learn to click through — and
then miss the one that had a permanent delegate. Warning fatigue does not make
people careful; it makes the warnings invisible.

So severity follows capability, not novelty:

  BLOCK — the token is built so someone else can take or trap your money.
    NonTransferable, PermanentDelegate, DefaultAccountState=frozen,
    freezeAuthority held by an unverified issuer.
    Confirm is disabled. An explicit acknowledgement re-enables it; we do not
    make the decision for an adult, but we do not let it be made by accident.

  WARN — a real cost or a real risk the user must weigh.
    transfer fee > 0 (with the number), mintAuthority live,
    top-10 holders > 80%, creator holds > 20%.
    Confirm requires a second, deliberate press.

  NOTE — context, shown without gating.
    unverified, new listing, low organic activity, mutable metadata.

The rule of thumb: if the finding describes something that can move the user's
money without their consent, it gates. If it describes the token being young
or small, it informs.

## Principle 4 — say what it means, not what it is called

"freezeAuthority: 7xKq…" is not a warning. "The issuer can freeze your wallet's
tokens, which means you may not be able to sell" is.

Every finding carries a plain sentence naming the consequence, in the user's
language. No field names, no addresses in the headline, no jargon that assumes
the reader knows what an authority is.

## What we cannot detect

Stated so nobody builds confidence on top of a gap:

  - Liquidity pulled after purchase. Nothing about the mint predicts it.
  - A team abandoning the project.
  - Social engineering: a real token promoted under false claims.
  - Off-chain custody promises.
  - A collection or token that is clean now and malicious after an upgrade
    (mutable metadata is flagged; the upgrade itself is not).

The card should never imply an absence of findings means safe. It means
nothing detectable was found.

## Phases

1. `token_safety(mint)` in the Rust service: mint-account parse, enriched by
   Birdeye and Shield, returning a severity and a list of plain-language
   findings. Cached briefly; authorities do not change often, and when they do
   the change is itself the event.
2. Enforcement on the signing path: every acquiring action calls it, the card
   renders the verdict, BLOCK gates Confirm behind an acknowledgement.
3. The conversational answer: "is X a scam" resolves to the same call, so the
   question and the guard never disagree.
