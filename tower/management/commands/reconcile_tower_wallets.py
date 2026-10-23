from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Student
from tower.models import Wallet, WalletEntry


class Command(BaseCommand):
    help = "Read-only reconciliation of cached balances and every ledger checkpoint."

    def handle(self, *args, **options):
        failures, checked = [], 0
        for wallet_id in Wallet.objects.order_by("id").values_list("id", flat=True).iterator():
            # Same lock order as mutations: stable Student, then Wallet.
            with transaction.atomic():
                student_id = Wallet.objects.values_list("player__student_id", flat=True).get(pk=wallet_id)
                Student.objects.select_for_update().get(pk=student_id)
                wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
                totals = {currency: 0 for currency in WalletEntry.Currency.values}
                for entry in wallet.entries.order_by("id").iterator():
                    totals[entry.currency] += entry.delta
                    if totals[entry.currency] != entry.balance_after:
                        failures.append(f"wallet={wallet_id} entry={entry.pk}: invalid checkpoint")
                for currency, total in totals.items():
                    if getattr(wallet, currency) != total:
                        failures.append(f"wallet={wallet_id} currency={currency}: ledger={total}, balance={getattr(wallet, currency)}")
            checked += 1
        if failures:
            raise CommandError("\n".join(failures))
        self.stdout.write(self.style.SUCCESS(f"Checked {checked} wallets: balances match the ledger."))
