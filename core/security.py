import hashlib
import hmac
import ipaddress
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import SecurityThrottle


def auth_version(pin_hash: str) -> str:
    payload = str(pin_hash or "").encode("utf-8", errors="replace")
    secret = settings.SECRET_KEY.encode("utf-8", errors="replace")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()

def client_ip(request) -> str:
    """Resolve the client IP without trusting attacker-controlled XFF entries."""

    forwarded = [
        item.strip()
        for item in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
        if item.strip()
    ]
    trusted_hops = max(0, int(getattr(settings, "TRUSTED_PROXY_HOPS", 0)))
    candidate = (
        forwarded[-trusted_hops]
        if forwarded and trusted_hops and len(forwarded) >= trusted_hops
        else request.META.get("REMOTE_ADDR", "")
    )
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "unknown"


def _key_hash(scope: str, identifier: str) -> str:
    payload = f"{scope}|{identifier}".encode("utf-8", errors="replace")
    secret = settings.SECRET_KEY.encode("utf-8", errors="replace")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _locked_bucket(scope: str, identifier: str):
    key_hash = _key_hash(scope, identifier)
    now = timezone.now()
    try:
        return SecurityThrottle.objects.select_for_update().get(key_hash=key_hash), now
    except SecurityThrottle.DoesNotExist:
        try:
            with transaction.atomic():
                bucket = SecurityThrottle.objects.create(
                    key_hash=key_hash,
                    scope=scope,
                    window_started_at=now,
                )
            return bucket, now
        except IntegrityError:
            return SecurityThrottle.objects.select_for_update().get(key_hash=key_hash), now


def is_limited(scope: str, identifier: str) -> bool:
    return SecurityThrottle.objects.filter(
        key_hash=_key_hash(scope, identifier),
        blocked_until__gt=timezone.now(),
    ).exists()


def record_hit(
    scope: str,
    identifier: str,
    *,
    limit: int,
    window_seconds: int,
    block_seconds: int,
    block_at_limit: bool = False,
) -> bool:
    """Record a hit and return True when this request must be rejected."""

    with transaction.atomic():
        bucket, now = _locked_bucket(scope, identifier)
        if bucket.blocked_until and bucket.blocked_until > now:
            return True

        if now - bucket.window_started_at >= timedelta(seconds=window_seconds):
            bucket.hits = 0
            bucket.window_started_at = now
            bucket.blocked_until = None

        bucket.hits += 1
        threshold_reached = bucket.hits >= limit if block_at_limit else bucket.hits > limit
        if threshold_reached:
            bucket.blocked_until = now + timedelta(seconds=block_seconds)

        bucket.save(
            update_fields=["hits", "window_started_at", "blocked_until", "updated_at"]
        )
        return threshold_reached


def clear_bucket(scope: str, identifier: str) -> None:
    SecurityThrottle.objects.filter(key_hash=_key_hash(scope, identifier)).delete()


def login_identifiers(kind: str, request, identity: str) -> list[tuple[str, str, int]]:
    normalized = (identity or "").strip().casefold()
    ip = client_ip(request)
    pair = f"{ip}|{normalized}"
    return [
        (f"login_{kind}_pair", pair, int(getattr(settings, "LOGIN_RATE_LIMIT_MAX_ATTEMPTS", 8))),
        (f"login_{kind}_identity", normalized, int(getattr(settings, "LOGIN_IDENTITY_MAX_ATTEMPTS", 12))),
        (f"login_{kind}_ip", ip, int(getattr(settings, "LOGIN_IP_MAX_ATTEMPTS", 40))),
    ]


def login_is_limited(kind: str, request, identity: str) -> bool:
    return any(
        is_limited(scope, identifier)
        for scope, identifier, _ in login_identifiers(kind, request, identity)
    )


def record_login_failure(kind: str, request, identity: str) -> None:
    window = int(getattr(settings, "LOGIN_RATE_LIMIT_WINDOW_SECONDS", 900))
    block = int(getattr(settings, "LOGIN_RATE_LIMIT_LOCK_SECONDS", 900))
    for scope, identifier, limit in login_identifiers(kind, request, identity):
        record_hit(
            scope,
            identifier,
            limit=limit,
            window_seconds=window,
            block_seconds=block,
            block_at_limit=True,
        )


def clear_login_identity(kind: str, request, identity: str) -> None:
    buckets = login_identifiers(kind, request, identity)
    for scope, identifier, _ in buckets[:2]:
        clear_bucket(scope, identifier)


def request_is_limited(
    scope: str,
    identifier: str,
    *,
    limit: int,
    window_seconds: int,
) -> bool:
    return record_hit(
        scope,
        identifier,
        limit=limit,
        window_seconds=window_seconds,
        block_seconds=window_seconds,
    )