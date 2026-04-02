"""Ensure all existing users have a UserSubscription record."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from subscriptions.models import UserSubscription

User = get_user_model()


class Command(BaseCommand):
    help = "Create UserSubscription records for existing users who don't have one."

    def handle(self, *args, **options):
        users_without = User.objects.exclude(subscription__isnull=False)
        created = 0
        for user in users_without:
            UserSubscription.objects.get_or_create(user=user)
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Created {created} subscription records."))
