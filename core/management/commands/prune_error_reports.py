from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.error_reports import drain_spool
from core.models import ServerErrorReport


class Command(BaseCommand):
    help = "Write spooled 502 reports to the database and delete old ones."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="Delete reports last seen more than N days ago.")
        parser.add_argument("--keep-unresolved", action="store_true", help="Never delete unresolved reports.")

    def handle(self, *args, days, keep_unresolved, **options):
        written = drain_spool()
        queryset = ServerErrorReport.objects.filter(last_seen_at__lt=timezone.now() - timedelta(days=days))
        if keep_unresolved:
            queryset = queryset.filter(is_resolved=True)
        deleted, _ = queryset.delete()
        self.stdout.write(f"spooled reports written: {written}; deleted: {deleted}")
