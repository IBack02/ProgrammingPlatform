# Collection rules v2

The active school offer uses immutable `school-v2`. Existing `school-v1` versions,
opening receipts and idempotent retries remain intact. Pack price stays 50 niscoins.

## Unlocks and odds

Common and rare characters require 10 fragments, epic characters 6, legendary
characters 1. An ordinary slot still supplies 2 fragments. A legendary slot supplies
1 fragment and therefore unlocks the character immediately. Surplus fragments still
become universal shards; exchange prices are unchanged.

The legendary probability applies **once per pack**, for any legendary character.
The selected character is uniform within the legendary pool. One successful roll
replaces one uniformly chosen slot; a pack cannot produce multiple legendary slots.

| Pack in cycle | Legendary probability |
| --- | --- |
| 1 | 0.60% |
| 2 | 1.09% |
| 3 | 1.58% |
| 4 | 2.07% |
| 5 | 2.56% |
| 6 | 3.04% |
| 7 | 3.53% |
| 8 | 4.02% |
| 9 | 4.51% |
| 10 | 5.00% |

The next pack starts again at 0.60%. A legendary drop does not reset this cycle.
`TowerPlayer.pack_cycle_position` records 0–9. The chance and cycle before/after are
saved in `GameOperation.result`. Duplicate requests and failed transactions do not
advance the cycle. The existing student/wallet row locks serialize concurrent packs.

Ordinary slots retain common:rare:epic weights 70:22:7. After four common-only
packs the first slot is rare or epic (22:7), unless replaced by a legendary. This
protection never increases the legendary probability. The journal shows conditional
ordinary-slot odds separately from the next pack's legendary chance and cycle number.

Migration `0012_collection_rules_v2` publishes the version, updates thresholds,
and automatically assembles qualifying old cards with once-only character-set grants.
Existing fragment balances are preserved, including balances above the new threshold.
Unpublished sets retain entitlements for later fulfilment. Disabled offers remain
disabled; a separately published newer offer is preserved. All accounts start their
new probability cycle at pack one when this migration is applied.

## Loading indicators

`page-loading.js` installs in the head before the student's initial inline requests.
It covers initial page loading, foreground lesson/dashboard/exam/peer requests and
visible images. It waits for response bodies and a render opportunity before hiding.
Concurrent requests share a counter. Failures and aborts release it; safety timeouts
prevent a permanently visible indicator. Game state polling, activity logging,
autosaves and integrity monitoring do not activate it.

The equipped `ui_loader` comes from the existing owned appearance context and asset
delivery URLs. Missing selections or failed image requests use a CSS spinner.
The default also works without Tower access or a TowerPlayer. The status does not
block controls, respects reduced motion and uses translated accessible status text.
No new images or Cloudflare uploads are required.

## Deployment and validation

Deploy source and new static files, then run the normal commands:

```sh
python manage.py migrate
python manage.py collectstatic --noinput
```

Validation: 267 Tower tests on SQLite (15 database-specific skips), plus 15 tests
on an isolated PostgreSQL instance, including concurrent identical/distinct pack
requests and character-set grants. System checks and migration drift checks pass.
Browser checks cover the real portal with delayed responses, the default indicator,
the equipped SVG delivered from R2, and fallback/error/abort cases in an isolated
local fixture. No production accounts or balances are modified during QA.
