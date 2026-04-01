from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from sports.models import Sport
from .models import UserProfile


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class PreferencesForm(forms.ModelForm):
    SPORT_CHOICES = [(s[0], s[1]) for s in Sport.choices]
    sports_followed = forms.MultipleChoiceField(
        choices=SPORT_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Sports to follow",
        help_text="Leave all unchecked to follow all sports.",
    )

    class Meta:
        model = UserProfile
        fields = ("sports_followed", "email_alerts", "min_edge_alert", "show_player_props", "dark_mode")
        widgets = {
            "min_edge_alert": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "0.5"}),
        }
