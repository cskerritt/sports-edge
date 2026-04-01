from django.contrib import admin
from .models import BetRecord, BankrollSnapshot, UserBankrollSettings


@admin.register(BetRecord)
class BetRecordAdmin(admin.ModelAdmin):
    list_display = (
        "description", "user", "sport", "is_yes", "amount_wagered",
        "entry_price", "outcome", "profit_loss", "placed_at"
    )
    list_filter = ("sport", "outcome", "is_yes")
    search_fields = ("description", "user__username")
    date_hierarchy = "placed_at"
    raw_id_fields = ("contract",)


@admin.register(BankrollSnapshot)
class BankrollSnapshotAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "starting_balance", "ending_balance", "total_profit_loss", "active_bets")
    list_filter = ("user",)
    date_hierarchy = "date"


@admin.register(UserBankrollSettings)
class UserBankrollSettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "initial_bankroll", "current_balance", "kelly_fraction", "edge_threshold")
