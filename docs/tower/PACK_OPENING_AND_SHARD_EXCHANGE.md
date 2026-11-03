# Booster reveals and shard exchange

## Pack presentation

After the server completes a pack purchase, the saved receipt shows a sealed foil
NIS booster. Clicking it tears off the upper seal. Three cards emerge from its
center with their backs visible. Each card turns once when clicked or activated
with the keyboard. Epic cards have violet edge light; legendary cards have gold
edge light and rotating rays. Reduced-motion preferences disable movement while
retaining the reveal steps and rarity colors. The booster is native HTML/CSS;
existing portraits and frames continue to use their Cloudflare asset URLs.

The opening animation sends no purchase requests. All outcomes, fragment grants
and duplicate rewards are committed before it appears. Session storage remembers
the torn seal and revealed cards per player/receipt. Reload resumes that state.
After all three cards have been turned, Continue shows the saved receipt. There
are no skip or replay controls; completed receipts remain visible on later visits.
Without JavaScript, the normal receipt remains visible. Storage is optional.

## Economy

The school offer now uses immutable `school-v3`; pack price remains 50 niscoins.
Only surplus fragments generate shards. Unlock thresholds, slot quantities,
legendary chance cycle and rare-slot protection remain unchanged. Previously
earned shards and old receipts are preserved. Buying character fragments with
shards is no longer available. Character fragments drop from packs; shards can
still be converted to niscoins at the 1:5 rate.

| Rarity | Shards per surplus fragment | Fragments per drop | Fully duplicate drop | At 5 niscoins per shard |
| --- | ---: | ---: | ---: | ---: |
| Common | 1 | 2 | 2 shards | 10 niscoins |
| Rare | 2 | 2 | 4 shards | 20 niscoins |
| Epic | 4 | 2 | 8 shards | 40 niscoins |
| Legendary | 16 | 1 | 16 shards | 80 niscoins |

The doubling of value per full duplicate drop accounts for the legendary slot's
single fragment. Partial duplicates pay only for the actual surplus quantity.
For a completely assembled collection, the expected return over the repeating
10-pack legendary cycle is about 44.9642 niscoins per 50-niscoin pack, including
the existing common-only protection. This assumes every earned shard is converted.
Ordinary incomplete collections return less because needed fragments are retained.
An individual rare pack can return more than its purchase cost.

The Wallet tab converts a positive whole number of shards at the server-owned
rate of 5. One transaction debits shards and credits niscoins with two entries in
one operation, plus audits. The existing student/wallet locks serialize exchanges
with packs and other wallet changes. Idempotent request IDs prevent duplicate
application; changed retry quantities conflict. A failed leg, insufficient shards
or a balance cap rolls back the entire exchange. Access, journal unlock and CSRF
checks apply; client-supplied exchange rates are rejected.

The former fragment-exchange form and its price preview have been removed.
Legacy HTML/API requests and service calls are rejected with
`fragment_exchange_disabled` before any debit or grant. Historical exchange
ledger entries, fragment totals and acquired character sets remain intact.

Migration `0014_pack_rewards_and_conversion` adds the ledger source label and
publishes the frozen v3 config. Disabled offers and separately published newer
offers remain intact. New rates apply to new purchases after migration.

## Deployment and verification

Push source, templates, new CSS/JS, tests and migration, then run:

```sh
python manage.py migrate
python manage.py collectstatic --noinput
```

No new environment variables or artwork upload is required. Validation covers
279 Tower tests on SQLite (17 database-specific skips), plus 22 PostgreSQL exchange
and pack concurrency checks. Browser QA uses a separate fake-account database:
normal/reduced motion, seal tear, epic/legendary flips, resume, completion,
keyboard activation, 320/390 px layouts, and an actual 3-shard → 15-niscoin exchange.
Normal motion was exercised in an isolated preview because the test browser itself
requests reduced motion. Preview overrides are not part of application code.

## Loader motion and return navigation

Each character loader now has its own motion, shared by the live loading indicator,
appearance editor and teacher set preview. The coder orbit spins; the geometry
rocks and scales; the flask shakes; the robot bounces; the palette swings; the leaf
sways; the wave stretches; the speech bubble pulses; the planet rotates on a
breathing orbit; the note moves left and right; the chess piece fades in and out;
and the gear rotates in steps. Default loading uses the existing spinner. Reduced
motion keeps the symbols still and visible.

These are CSS presentation profiles in `tower/ui_loaders.py` and
`loader-animations.css`; existing published set metadata and R2 artwork are
unchanged. No migration or artwork upload is needed for these adjustments.
The journal and its password page return to `/student/dashboard/`.
