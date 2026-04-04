"""
Management command: fix_contract_names

Fixes existing MarketContract records:
1. Resolves abbreviated Kalshi city names to full team names in titles
2. Re-links contracts to the correct Game records (requires BOTH teams match)

Usage:
    python manage.py fix_contract_names           # dry-run (shows what would change)
    python manage.py fix_contract_names --apply    # apply changes
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from markets.kalshi import (
    _build_clean_title,
    _parse_kalshi_title,
    _resolve_kalshi_team,
    _team_matches_game,
)
from markets.models import MarketContract, MarketSource
from sports.models import Game

logger = logging.getLogger(__name__)


def _find_correct_game(contract: MarketContract) -> int | None:
    """Find the correct Game for a contract by matching BOTH teams."""
    if not contract.game_date:
        return None

    sport = contract.sport
    team_a, team_b = _parse_kalshi_title(contract.title, sport)
    if not team_b:
        # Can't parse two teams from title — try the already-clean title
        team_a, team_b = _parse_kalshi_title(
            contract.title.replace("Winner?", "").strip(), sport
        )
    if not team_a or not team_b:
        return None

    search_names = {team_a.upper(), team_b.upper()}

    window_start = contract.game_date - timedelta(days=1)
    window_end = contract.game_date + timedelta(days=1)

    qs = Game.objects.filter(
        sport=sport,
        game_date__range=(window_start, window_end),
    ).select_related("home_team", "away_team")

    for game in qs:
        home_matched = _team_matches_game(search_names, game.home_team)
        away_matched = _team_matches_game(search_names, game.away_team)

        if home_matched and away_matched:
            return game.pk

    return None


class Command(BaseCommand):
    help = "Fix contract titles (resolve abbreviated team names) and re-link to correct games"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually apply changes (default is dry-run)",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        contracts = MarketContract.objects.all().select_related(
            "game__home_team", "game__away_team"
        )

        title_fixes = 0
        game_fixes = 0
        game_clears = 0

        for contract in contracts:
            old_title = contract.title
            sport = contract.sport
            updates = []

            # --- Fix title ---
            team_a, team_b = _parse_kalshi_title(old_title, sport)
            if team_b:
                new_title = _build_clean_title(team_a, team_b, sport)
                if new_title != old_title:
                    if apply:
                        contract.title = new_title
                    updates.append(f"title: '{old_title}' → '{new_title}'")
                    title_fixes += 1

            # --- Fix game link ---
            # Use the NEW (clean) title for game matching
            correct_game_id = _find_correct_game(contract)

            if correct_game_id and correct_game_id != contract.game_id:
                old_game = contract.game
                old_label = f"{old_game}" if old_game else "None"
                if apply:
                    contract.game_id = correct_game_id
                new_game = Game.objects.select_related("home_team", "away_team").get(pk=correct_game_id)
                updates.append(f"game: {old_label} → {new_game}")
                game_fixes += 1
            elif not correct_game_id and contract.game_id:
                # Current link is likely wrong — clear it
                old_game = contract.game
                if apply:
                    contract.game_id = None
                updates.append(f"game: {old_game} → None (no correct match found)")
                game_clears += 1

            if updates and apply:
                contract.save(update_fields=["title", "game_id"])

            if updates:
                self.stdout.write(
                    f"  [{sport}] {old_title}"
                )
                for u in updates:
                    self.stdout.write(f"    → {u}")

        mode = "APPLIED" if apply else "DRY RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{mode}: {title_fixes} titles fixed, "
                f"{game_fixes} games re-linked, "
                f"{game_clears} wrong links cleared"
            )
        )
        if not apply:
            self.stdout.write(
                self.style.WARNING("  Run with --apply to save changes.")
            )
