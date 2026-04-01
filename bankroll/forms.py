from django import forms

from sports.models import Sport

from .models import BetOutcome, BetRecord, UserBankrollSettings


class BetRecordForm(forms.ModelForm):
    """
    Form for logging a new bet.

    ``contract`` is optional – users may enter a raw Coinbase product ID or
    leave it blank if they cannot link the bet to a known MarketContract.
    """

    class Meta:
        model = BetRecord
        fields = (
            "contract",
            "sport",
            "description",
            "is_yes",
            "amount_wagered",
            "entry_price",
            "predicted_edge",
            "kelly_fraction_used",
            "notes",
        )
        widgets = {
            "entry_price": forms.NumberInput(attrs={
                "step": "0.001",
                "min": "0.001",
                "max": "0.999",
                "placeholder": "0.550",
            }),
            "amount_wagered": forms.NumberInput(attrs={
                "step": "0.01",
                "min": "0.01",
                "placeholder": "10.00",
            }),
            "predicted_edge": forms.NumberInput(attrs={
                "step": "0.001",
                "placeholder": "0.080",
            }),
            "kelly_fraction_used": forms.NumberInput(attrs={
                "step": "0.001",
                "min": "0",
                "max": "1",
                "placeholder": "0.25",
            }),
            "description": forms.TextInput(attrs={"placeholder": "e.g. Chiefs ML vs Raiders"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # contract is optional
        self.fields["contract"].required = False
        self.fields["contract"].help_text = (
            "Optional – link to a Coinbase market contract."
        )
        self.fields["predicted_edge"].required = False
        self.fields["kelly_fraction_used"].required = False
        self.fields["notes"].required = False
        # Restrict sport choices to those defined in Sport.choices
        self.fields["sport"].widget = forms.Select(choices=Sport.choices)


class SettleBetForm(forms.ModelForm):
    """
    Minimal form used to settle (resolve) an existing bet.
    Exposed via the bet_detail page and the HTMX settle_bet endpoint.
    """

    class Meta:
        model = BetRecord
        fields = ("outcome", "profit_loss", "settled_at", "notes")
        widgets = {
            "settled_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "profit_loss": forms.NumberInput(attrs={
                "step": "0.01",
                "placeholder": "e.g. 45.00 (win) or -10.00 (loss)",
            }),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Exclude PENDING from the outcome choices here – settling means
        # moving away from PENDING.
        settled_choices = [
            (k, v) for k, v in BetOutcome.choices
            if k != BetOutcome.PENDING
        ]
        self.fields["outcome"].choices = settled_choices
        self.fields["profit_loss"].required = False
        self.fields["settled_at"].required = False
        self.fields["notes"].required = False
        # Pre-populate settled_at input format
        if self.instance and self.instance.settled_at:
            self.initial["settled_at"] = (
                self.instance.settled_at.strftime("%Y-%m-%dT%H:%M")
            )


class BankrollSettingsForm(forms.ModelForm):
    """Form for editing the user's bankroll configuration."""

    class Meta:
        model = UserBankrollSettings
        fields = (
            "initial_bankroll",
            "current_balance",
            "kelly_fraction",
            "max_bet_pct",
            "edge_threshold",
        )
        widgets = {
            "initial_bankroll": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "current_balance": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "kelly_fraction": forms.NumberInput(attrs={
                "step": "0.01",
                "min": "0.01",
                "max": "1.00",
                "placeholder": "0.25",
            }),
            "max_bet_pct": forms.NumberInput(attrs={
                "step": "0.005",
                "min": "0.005",
                "max": "1.00",
                "placeholder": "0.05",
            }),
            "edge_threshold": forms.NumberInput(attrs={
                "step": "0.005",
                "min": "0",
                "placeholder": "0.05",
            }),
        }
        help_texts = {
            "kelly_fraction": "Fraction of full-Kelly to use. 0.25 = quarter-Kelly (recommended).",
            "max_bet_pct": "Hard cap per bet as a fraction of current balance (e.g. 0.05 = 5%).",
            "edge_threshold": "Minimum model edge to trigger an EdgeAlert (e.g. 0.05 = 5%).",
        }
