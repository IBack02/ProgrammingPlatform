# Security Audit Report

Date: 2026-07-22  
Project: ProgrammingPlatform  
Scope: Django application code, templates, API routes, authentication, authorization, external integrations, settings, migrations, and Python dependencies.

## Executive Summary

This was a source-code security audit and regression-hardening pass guided by OWASP API Security Top 10 techniques and the local `conducting-api-security-testing` skill from `mukul975/Anthropic-Cybersecurity-Skills`.

Critical and high-risk issues found in the inspected code were fixed. The most important changes are persistent brute-force protection, revocable student/teacher sessions, elimination of quiz answer disclosure, ownership checks, race-safe submissions, safe AI endpoints, output encoding, and production browser security controls.

This is not a substitute for an authorized live penetration test against the deployed Render environment. Render networking, DNS, TLS termination, Postgres permissions, Google Drive ACLs, and third-party dashboards cannot be fully proven from source code.

## Scope and Method

Reviewed components:

- Django settings, middleware, URL mappings, views, models, and migrations.
- Student, teacher, and Django admin authentication.
- Teacher object ownership and cross-account access control.
- Student session, coding submission, theory quiz, and exam workflows.
- OpenAI, Judge0, Google Drive, YouTube, and draw.io integration boundaries.
- Template and JavaScript output encoding.
- Request size limits, rate limits, CSRF, session cookies, TLS settings, and security headers.
- Dependency versions using `pip-audit`.
- ORM use and possible raw SQL injection surfaces.
- Duplicate endpoint definitions, decorators, and missing URL handlers.

Verification performed:

- `python manage.py check`
- `python manage.py makemigrations --check --dry-run`
- `python manage.py test core`
- `python manage.py check --deploy`
- `python -m pip check`
- `python -m pip_audit -r requirements.txt --no-deps --disable-pip`
- AST scan for duplicate view definitions, duplicate decorators, and missing URL handlers.
- Static search for raw SQL, dangerous execution APIs, and committed secrets.

## Fixed Findings

### Critical: Quiz Matching Answer Disclosure

Previous matching item identifiers could reveal correct pairs because both columns exposed related database identifiers.

Fix:

- Student-visible matching identifiers are now opaque HMAC tokens.
- Tokens are bound to the student, question, pair, and side.
- Submission validation resolves only authentic tokens.
- A regression test proves that left and right identifiers are disjoint and correct answers still score normally.

Status: fixed and tested.

### Critical: Brute Force Against Six-Digit PINs

Six-digit PINs have low entropy. Per-process memory throttling is ineffective on multi-worker Render deployments and resets after restart.

Fix:

- Added the `SecurityThrottle` database model.
- Added transactional Postgres-compatible rate buckets.
- Student and teacher login limits now cover IP plus identity, identity globally, and IP globally.
- Django admin login POST requests are rate limited.
- Keys are stored as HMAC hashes, not raw names or IP addresses.
- Response code `429` and `Retry-After` are returned when blocked.

Status: fixed at the application layer and tested.

### Critical: Vulnerable or Unbounded Direct Dependencies

The dependency file did not provide a sufficiently controlled production baseline and the previously installed Django version was behind security releases.

Fix:

- Pinned direct runtime dependencies to reviewed versions.
- Django is pinned to `5.2.16`.
- The final dependency audit reports no known vulnerabilities.
- `pip check` reports no broken requirements.

Status: fixed for direct dependencies. Hash locking remains recommended.

### High: Sessions Survived PIN Reset or Account Changes

A copied authenticated session could remain usable after a PIN reset.

Fix:

- Student and teacher sessions contain an HMAC version derived from the current PIN hash.
- Every protected request compares the session version in constant time.
- PIN reset or PIN change invalidates all existing sessions.
- Inactive or deleted accounts are rejected.
- Student class membership is revalidated against the database rather than trusted from session state.

Status: fixed and tested. Existing sessions will be logged out once after deployment.

### High: Cross-Teacher Object Access

Teacher APIs must not allow one teacher to access another teacher's sessions, classes, students, modules, or exam data by changing an object ID.

Fix:

- Ownership-scoped querysets and object lookups are retained and regression-tested.
- Requests for another teacher's private session return `404`, avoiding resource enumeration.
- Stale or inactive teacher sessions are rejected centrally.

Status: fixed and tested for representative IDOR/BOLA paths.

### High: Parallel Coding Submission Race

Concurrent requests could bypass cooldown and duplicate-code controls or produce conflicting attempt numbers.

Fix:

- Progress rows are locked with `select_for_update()`.
- Cooldown, code hash, and attempt reservation happen atomically before Judge0 is called.
- Final progress and submission updates are atomic.
- Source code size is limited to 100 KiB.
- Judge0 failures return generic API messages while details stay in server logs.

Status: fixed. A Postgres concurrency load test is still recommended.

### High: AI Hint Cost and CSRF Abuse

A state-changing hint-generation flow could be triggered by GET and parallel requests could create duplicate paid calls.

Fix:

- New hint generation requires POST with CSRF protection.
- GET remains compatible only for already cached hints and cannot generate or incur cost.
- Per-progress generation state prevents parallel duplicate generation.
- Hourly quotas protect hint, theory-generation, quiz-evaluation, and other expensive paths.
- Hint 3 remains student-specific and does not mark the task solved.

Status: fixed.

### High: Exam Save and Submit Race

An answer update racing with final submission could mutate an already submitted exam.

Fix:

- Exam attempts are transactionally locked before answer changes.
- Submitted or expired attempts reject further writes.
- Final submission fixes database state before optional external processing.
- Diagram save endpoints have request quotas.

Status: fixed.

### High: Stored and DOM XSS

Chart data and selected JavaScript rendering paths could allow HTML or script-context breakout from teacher-controlled text.

Fix:

- Chart payloads use Django `json_script` instead of `safe` JSON.
- Student-side escaping now includes quotes and apostrophes.
- Theory media URLs reject unsafe schemes, credentials, control characters, and non-HTTPS input.
- Video embeds only accept approved YouTube hosts.
- CSP, frame restrictions, MIME sniffing protection, and a restrictive permissions policy are set globally.

Status: fixed for confirmed paths. CSP still permits inline scripts as a migration compatibility measure.

### Medium: Open Redirect in Language Selection

The language endpoint accepted an untrusted next URL.

Fix:

- Redirect targets are allowed only when same-host and scheme-safe.
- Unsafe external targets fall back to an internal portal page.

Status: fixed and tested.

### Medium: API Error Leakage and HTML Responses

Unhandled API exceptions could expose technical details or return HTML that frontend code attempted to parse as JSON.

Fix:

- API exceptions are normalized to JSON.
- `404`, oversized body, suspicious request, CSRF, and unexpected failures have controlled status codes and messages.
- Unexpected exceptions are logged server-side without returning stack traces.
- Failed API responses use `Cache-Control: no-store`.

Status: fixed and tested.

### Medium: Request and Resource Exhaustion

Large JSON/code payloads and repeatedly invoked expensive integrations could consume memory or paid API quota.

Fix:

- Global request body and in-memory file limits were added.
- Coding source size is checked separately.
- Database-backed request quotas cover expensive endpoints.
- Login throttling is shared across workers.

Status: materially reduced.

### Medium: Transport and Browser Hardening

Transport security depended on deployment defaults.

Fix:

- Production forces HTTPS.
- Secure session and CSRF cookies are enabled.
- Sessions are HTTP-only, SameSite Lax, limited to 12 hours, and expire on browser close.
- HSTS is one year with subdomains.
- Clickjacking, MIME sniffing, opener, referrer, permissions, CSP, and cross-domain policy controls are configured.
- Postgres uses SSL when `DATABASE_URL` is present.

Status: fixed in application configuration. Render environment settings must still be verified.

## Not Found in Static Audit

- No application raw SQL execution path was found; Django ORM parameterization is used.
- No committed OpenAI, Judge0, or Google private key was found in tracked source.
- No unsafe Python `eval`, `exec`, shell command, pickle deserialization, or YAML deserialization path was found.
- No conflicting duplicate endpoint definitions or duplicate decorators remain.
- Every handler referenced by `core/urls.py` exists.
- Browser media URLs are not fetched by the Django server, so the reviewed image/video feature did not introduce a direct server-side SSRF path.

These statements apply only to the audited repository state, not to external dashboards, old Git history, logs, backups, or runtime environment variables.

## Residual Risks and Recommendations

### High: Six-Digit PIN Authentication

Rate limiting reduces online brute force but cannot make a six-digit PIN equivalent to a strong password.

Recommended next step:

- Add stronger teacher authentication first: password plus MFA, passkey, or an external identity provider.
- Consider a second factor or class/session access code for students.
- Alert on distributed attempts across many IP addresses.

### Medium: CSP Allows Inline Script and Style

The current templates contain substantial inline JavaScript and CSS, so CSP temporarily includes `'unsafe-inline'`.

Recommended next step:

- Move scripts and styles to static files.
- Replace inline handlers.
- Adopt CSP nonces or hashes.
- Remove `'unsafe-inline'` after browser regression testing.

### Medium: Dependency Integrity

Versions are pinned but hashes are not.

Recommended next step:

- Generate a hash-locked deployment file with `pip-compile --generate-hashes`.
- Run `pip-audit` in CI for every pull request and scheduled build.
- Enable automated dependency update review.

### Medium: Distributed Denial of Service

Application quotas do not replace edge protection and can still cause database work.

Recommended next step:

- Enable Render or upstream WAF/rate controls.
- Add Redis-backed throttling if traffic grows.
- Add retention cleanup for expired `SecurityThrottle` rows.
- Monitor spikes in `429`, login failures, OpenAI calls, Judge0 calls, and exam writes.

### Medium: Third-Party Data and Secrets

OpenAI, Judge0, Google Drive, YouTube, and draw.io create external trust boundaries.

Recommended next step:

- Rotate and scope API keys.
- Restrict Google Drive service-account permissions to one dedicated folder.
- Keep `GOOGLE_DRIVE_EXAM_MAKE_PUBLIC=false` unless public access is explicitly required.
- Verify that no student personal data or secrets are included in AI prompts.
- Set vendor quota and billing alerts.
- Review third-party retention and data-processing terms.

### Medium: Production Concurrency Coverage

SQLite unit tests do not reproduce all Postgres row-lock and isolation behavior.

Recommended next step:

- Run authenticated concurrent submit, hint, quiz, and exam tests against a disposable Postgres environment.
- Verify one accepted write, deterministic attempt numbering, and correct `409` or `429` responses.

### Low: HSTS Preload

`SECURE_HSTS_PRELOAD` remains false intentionally. Enabling preload without control of every subdomain can cause an outage.

Recommended next step:

- Enable it only after confirming all present and future subdomains are permanently HTTPS-capable and the domain owner accepts preload consequences.

## Deployment Checklist

1. Back up the Render Postgres database.
2. Deploy the reviewed dependency versions.
3. Run `python manage.py migrate` to create `SecurityThrottle`.
4. Confirm `DEBUG=false` and a strong unique `SECRET_KEY`.
5. Set exact `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`; avoid broad wildcard values where possible.
6. Confirm `TRUSTED_PROXY_HOPS=1` matches the actual Render proxy chain before relying on IP throttling.
7. Verify HTTPS redirect, HSTS, cookies, CSP, and API JSON errors from the public URL.
8. Rotate OpenAI, Judge0, and Google credentials if they have ever appeared in logs, chat, old commits, or local files.
9. Expect existing teacher and student sessions to be invalidated once because session auth versioning is new.
10. Monitor logs and database health during the first deployment window.

## Verification Result

At audit completion:

- Django system check: passed.
- Migration consistency check: passed.
- Core test suite: 14 tests passed.
- Dependency consistency: passed.
- Dependency vulnerability audit: no known vulnerabilities found.
- URL/view consistency: passed.
- Duplicate definition/decorator scan: passed.
- Production deployment check: only the intentional HSTS preload warning remains.
