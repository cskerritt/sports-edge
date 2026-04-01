from django.contrib.auth.models import User
from django.db import models
from sports.models import Sport


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    # Which sports to show in dashboard (empty = show all)
    sports_followed = models.JSONField(
        default=list,
        blank=True,
        help_text="List of sport codes, e.g. ['NFL', 'NBA']. Empty = all sports.",
    )
    # Alert preferences
    email_alerts = models.BooleanField(default=False)
    min_edge_alert = models.FloatField(
        default=0.05, help_text="Only show alerts with edge ≥ this value."
    )
    # Display preferences
    show_player_props = models.BooleanField(default=True)
    dark_mode = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s profile"

    @property
    def active_sports(self):
        if self.sports_followed:
            return self.sports_followed
        return [s[0] for s in Sport.choices]

    def follows_sport(self, sport_code):
        if not self.sports_followed:
            return True
        return sport_code in self.sports_followed
