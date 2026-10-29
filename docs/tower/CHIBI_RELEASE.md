# Supplied chibi artwork and floating companion

All 12 supplied transparent PNGs were renamed to their canonical character IDs in
`docs/chibi/`, then imported without changing any pixels. They retain their original
1254×1254 RGBA dimensions and reside at `sets/<hero>/v2/companion.png` in static assets.
`docs/chibi/README.md` lists the character mapping. No image generation was used.

The complete v2 sets preserve immutable revision directories, stable cosmetic IDs,
attachment geometry, behavior profiles and existing ownership. Original v1 resources
remain for historical publications. Migration `0013_publish_chibi_art` publishes the
new snapshots and updates cosmetic definitions, including already owned companions.
It preserves paused/newer publications, does not award duplicate sets, and does not
spend or alter currency. Saved selections automatically display the new PNGs.

All 72 v2 resources were uploaded to the existing Cloudflare R2 bucket and verified
via public GET against their local lengths and SHA-256 hashes. The active public base
and Render environment variables remain unchanged. Local publication evidence is in
`artifacts/tower/character-sets/r2-chibi-publication.json` (no credentials).

The old full-width personal strip and its appearance link are removed. A transparent
chibi summary sits at the bottom right and opens its companion card on click or
keyboard activation. The card closes using its close button, Escape or an outside
click. It starts collapsed on each page. Appearance selection remains available in
the journal and inside the opened companion card. No companion selection produces
no empty footer or floating box.

The red `!` marks a ready reply or an unread stored reply. Reading a stored reply
acknowledges it and removes the unread badge; cooldown and explicit generation rules
remain intact. Games/exams hide the companion. Mobile sizes use a smaller sprite and
keep the card inside the viewport. Loading indicators sit above the companion.

Deployment: push the new static v2 directories, manifest, code and migration, then:

```sh
python manage.py migrate
python manage.py collectstatic --noinput
```

Validation: 270 Tower tests on SQLite (15 database-specific skips), 33 tests on an
isolated PostgreSQL instance, system/migration/contract checks, JavaScript syntax,
and byte-for-byte comparison of all 12 inputs and imported PNGs. Browser checks used
the real templates/assets and a local fake companion response, with no AI calls:
320, 390, 768 and 1280 px, status, opening/closing, reply acknowledgement, and game/
exam hiding. Main workspace and production databases were not used for preview QA.
