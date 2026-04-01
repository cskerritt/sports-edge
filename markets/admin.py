from django.contrib import admin
from .models import MarketContract, MarketPrice, EdgeAlert


class MarketPriceInline(admin.TabularInline):
    model = MarketPrice
    extra = 0
    readonly_fields = ("yes_price", "no_price", "mid_price", "volume_24h", "fetched_at")
    max_num = 10
    ordering = ("-fetched_at",)


@admin.register(MarketContract)
class MarketContractAdmin(admin.ModelAdmin):
    list_display = (
        "title", "sport", "contract_type", "game_date", "is_active", "is_resolved", "resolution"
    )
    list_filter = ("sport", "contract_type", "is_active", "is_resolved")
    search_fields = ("title", "coinbase_product_id")
    date_hierarchy = "game_date"
    raw_id_fields = ("game",)
    inlines = (MarketPriceInline,)


@admin.register(MarketPrice)
class MarketPriceAdmin(admin.ModelAdmin):
    list_display = ("contract", "yes_price", "no_price", "mid_price", "volume_24h", "fetched_at")
    list_filter = ("contract__sport",)
    date_hierarchy = "fetched_at"
    raw_id_fields = ("contract",)


@admin.register(EdgeAlert)
class EdgeAlertAdmin(admin.ModelAdmin):
    list_display = (
        "contract", "sport", "edge_pct_display", "model_probability", "market_probability",
        "kelly_pct_display", "confidence", "status", "created_at"
    )
    list_filter = ("sport", "status")
    ordering = ("-edge",)
    date_hierarchy = "created_at"
    readonly_fields = ("edge", "created_at", "resolved_at")

    @admin.display(description="Edge %")
    def edge_pct_display(self, obj):
        return f"{obj.edge_pct:+.2f}%"

    @admin.display(description="Kelly %")
    def kelly_pct_display(self, obj):
        return f"{obj.kelly_pct:.2f}%"
