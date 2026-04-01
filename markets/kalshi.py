"""
Kalshi Trade API v2 client for prediction market contracts.

API base: https://api.elections.kalshi.com/trade-api/v2
Binary markets settle at $1.00 for YES outcomes.
Prices are quoted in dollar strings ("0.4900"); we convert to floats (0.0–1.0).

Public GET endpoints work without authentication.
The KALSHI_API_KEY is sent as a Bearer token for private endpoints if present.
"""

import logging
from datetime import date

import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime

from markets.models import ContractType, MarketContract, MarketPrice, MarketSource

logger = logging.getLogger("markets.kalshi")

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Sports game-winner series tickers → sport code
GAME_SERIES: dict[str, str] = {
    "KXMLBGAME": "MLB",
    "KXNBAGAME": "NBA",
    "KXNFLGAME": "NFL",
    "KXNHLGAME": "NHL",
}

# Additional sports series for award/championship markets
SPORT_SERIES_EXTRA: dict[str, str] = {
    "KXMLB": "MLB",
    "KXNBA": "NBA",
    "KXNFL": "NFL",
    "KXNHL": "NHL",
}


def _infer_sport_from_ticker(ticker: str) -> str:
    """Return sport code from a series or event ticker, or '' if unknown."""
    upper = ticker.upper()
    for prefix, sport in GAME_SERIES.items():
        if upper.startswith(prefix):
            return sport
    for prefix, sport in SPORT_SERIES_EXTRA.items():
        if upper.startswith(prefix):
            return sport
    return ""


def _dollars_to_prob(price_str: str | float | None) -> float:
    """Convert a Kalshi dollar price string (e.g. '0.4900') to a 0.0–1.0 probability."""
    if price_str is None:
        return 0.5
    try:
        p = float(price_str)
    except (TypeError, ValueError):
        return 0.5
    return max(0.01, min(p, 0.99))


def _mid_price_from_market(m: dict) -> tuple[float, float, float]:
    """Compute (yes_price, no_price, mid_price) from a Kalshi market dict.

    Uses yes_bid_dollars / yes_ask_dollars.  Falls back to last_price_dollars.
    """
    yes_bid = m.get("yes_bid_dollars")
    yes_ask = m.get("yes_ask_dollars")
    no_bid = m.get("no_bid_dollars")
    no_ask = m.get("no_ask_dollars")
    last = m.get("last_price_dollars")

    # YES mid price
    if yes_bid is not None and yes_ask is not None:
        yes_price = _dollars_to_prob((float(yes_bid) + float(yes_ask)) / 2.0)
    elif yes_bid is not None:
        yes_price = _dollars_to_prob(yes_bid)
    elif yes_ask is not None:
        yes_price = _dollars_to_prob(yes_ask)
    elif last is not None:
        yes_price = _dollars_to_prob(last)
    else:
        return 0.5, 0.5, 0.5

    no_price = max(0.01, min(1.0 - yes_price, 0.99))

    # Spread-adjusted mid: (YES bid + NO bid) / 2
    if yes_bid is not None and no_bid is not None:
        mid_price = _dollars_to_prob((float(yes_bid) + float(no_bid)) / 2.0)
    else:
        mid_price = yes_price

    return yes_price, no_price, mid_price


class KalshiPredictionClient:
    """
    Client for the Kalshi Trade API v2.

    Public GET endpoints are used for market discovery and price reads —
    no authentication is required for these. The API key, if present, is
    sent as a Bearer token header for any private endpoints.
    """

    def __init__(self):
        self.api_key = getattr(settings, "KALSHI_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        if self.api_key:
            self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        url = BASE_URL + path
        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            logger.error("Kalshi HTTP %s for %s: %s", exc.response.status_code, path, exc)
        except requests.exceptions.RequestException as exc:
            logger.error("Kalshi request error for %s: %s", path, exc)
        except ValueError as exc:
            logger.error("Kalshi JSON decode error for %s: %s", path, exc)
        return None

    def _paginate(self, path: str, key: str, params: dict | None = None) -> list[dict]:
        """Fetch all pages for a Kalshi list endpoint using cursor pagination."""
        params = dict(params or {})
        params.setdefault("limit", 200)
        results: list[dict] = []
        while True:
            data = self._get(path, params=params)
            if not data:
                break
            page = data.get(key, [])
            results.extend(page)
            cursor = data.get("cursor", "")
            if not cursor or not page:
                break
            params["cursor"] = cursor
        return results

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_game_markets(self) -> list[dict]:
        """Fetch all active game-winner markets for MLB, NBA, NFL, NHL.

        Queries each game-winner series in GAME_SERIES and returns the combined
        list of market dicts.
        """
        all_markets: list[dict] = []
        for series_ticker, sport in GAME_SERIES.items():
            markets = self._paginate(
                "/markets",
                "markets",
                params={"series_ticker": series_ticker, "status": "open"},
            )
            if not markets:
                # Try "active" status in case the API changed
                markets = self._paginate(
                    "/markets",
                    "markets",
                    params={"series_ticker": series_ticker},
                )
            for m in markets:
                m["_sport"] = sport  # tag it for later
            all_markets.extend(markets)
            logger.info("  %s (%s): %d markets", series_ticker, sport, len(markets))

        logger.info("get_game_markets: total %d markets across all sports", len(all_markets))
        return all_markets

    def get_market(self, ticker: str) -> dict | None:
        """Fetch a single market by ticker."""
        data = self._get(f"/markets/{ticker}")
        if not data:
            return None
        return data.get("market", data)

    # ------------------------------------------------------------------
    # Game linking
    # ------------------------------------------------------------------

    def _try_link_game(self, market: dict, sport: str) -> int | None:
        """Try to link a Kalshi market to a Game record by date and team names.

        Checks team name, abbreviation, AND city against the market title and
        yes/no subtitles. Uses ``expected_expiration_time`` (closer to actual
        game time) with a ±2 day search window.

        Returns the Game pk if found, else None.
        """
        from sports.models import Game

        # Prefer expected_expiration_time (actual game time) over close_time
        game_date: date | None = None
        for field in ("expected_expiration_time", "close_time", "expiration_time"):
            ts = market.get(field)
            if ts:
                try:
                    dt = parse_datetime(ts)
                    if dt:
                        game_date = dt.date()
                        break
                except Exception:
                    pass

        if not game_date:
            return None

        yes_team = market.get("yes_sub_title", "")
        no_team = market.get("no_sub_title", "")
        title = market.get("title", "")
        checks = [yes_team.upper(), no_team.upper(), title.upper()]

        from datetime import timedelta
        window_start = game_date - timedelta(days=2)
        window_end = game_date + timedelta(days=2)

        qs = Game.objects.filter(
            sport=sport,
            game_date__range=(window_start, window_end),
        ).select_related("home_team", "away_team")

        for game in qs:
            # Build all possible identifiers for each team
            home_ids = set()
            away_ids = set()
            for team, ids in [(game.home_team, home_ids), (game.away_team, away_ids)]:
                if not team:
                    continue
                if team.name:
                    ids.add(team.name.upper())
                if team.abbreviation:
                    ids.add(team.abbreviation.upper())
                if team.city:
                    ids.add(team.city.upper())

            for check in checks:
                if any(ident in check for ident in home_ids if ident):
                    return game.pk
                if any(ident in check for ident in away_ids if ident):
                    return game.pk

        return None

    # ------------------------------------------------------------------
    # Price refresh
    # ------------------------------------------------------------------

    def fetch_and_store_prices(self) -> dict:
        """Refresh prices for all active Kalshi MarketContracts in the DB.

        Returns ``{"fetched": int, "errors": int}``.
        """
        contracts = MarketContract.objects.filter(
            source=MarketSource.KALSHI, is_active=True, is_resolved=False
        )
        fetched = 0
        errors = 0

        for contract in contracts:
            ticker = contract.coinbase_product_id.removeprefix("KALSHI:")
            try:
                market = self.get_market(ticker)
                if not market:
                    logger.debug("No data for Kalshi ticker %s — skipping.", ticker)
                    errors += 1
                    continue

                status = market.get("status", "")
                if status not in ("open", "active"):
                    updates: dict = {"is_active": False}
                    if status in ("resolved", "settled"):
                        updates["is_resolved"] = True
                        result_str = market.get("result", "")
                        updates["resolution"] = (result_str == "yes") if result_str else None
                    contract.__dict__.update(updates)
                    contract.save(update_fields=list(updates.keys()))
                    continue

                yes_price, no_price, mid_price = _mid_price_from_market(market)
                vol = market.get("volume_24h_fp") or market.get("volume_fp")
                oi = market.get("open_interest_fp")

                MarketPrice.objects.create(
                    contract=contract,
                    yes_price=yes_price,
                    no_price=no_price,
                    mid_price=mid_price,
                    volume_24h=float(vol) if vol else None,
                    open_interest=float(oi) if oi else None,
                )
                fetched += 1

            except Exception as exc:
                logger.error("Error refreshing %s: %s", ticker, exc)
                errors += 1

        logger.info("fetch_and_store_prices: fetched=%d errors=%d", fetched, errors)
        return {"fetched": fetched, "errors": errors}

    # ------------------------------------------------------------------
    # Contract discovery
    # ------------------------------------------------------------------

    def discover_and_create_contracts(self) -> dict:
        """Fetch open game markets from Kalshi and upsert MarketContract records.

        For each market not yet in the database:
        - Creates a MarketContract with source=KALSHI
        - Uses ``coinbase_product_id = "KALSHI:{ticker}"`` as the unique key
        - Creates an initial MarketPrice snapshot
        - Attempts to link to an existing Game record

        Returns ``{"created": int, "updated": int, "skipped": int}``.
        """
        markets = self.get_game_markets()
        created = updated = skipped = 0

        for m in markets:
            ticker = m.get("ticker", "")
            if not ticker:
                skipped += 1
                continue

            sport = m.get("_sport") or _infer_sport_from_ticker(
                m.get("series_ticker", "") or m.get("event_ticker", "")
            )
            if not sport:
                skipped += 1
                continue

            title = m.get("title", ticker)
            external_id = f"KALSHI:{ticker}"

            # Parse game date — use expected_expiration_time (closest to actual game)
            game_date = None
            expiry = None
            for date_field in ("expected_expiration_time", "close_time", "expiration_time"):
                ts = m.get(date_field)
                if ts:
                    try:
                        dt = parse_datetime(ts)
                        if dt:
                            if date_field == "expected_expiration_time":
                                game_date = dt.date()
                            if expiry is None:
                                expiry = dt
                            if game_date is None:
                                game_date = dt.date()
                    except Exception:
                        pass

            # Infer contract type from yes_sub_title (the team the YES side is for)
            # Every game market has exactly two markets (one per team);
            # the YES side wins if that team wins.
            contract_type = ContractType.HOME_WIN  # default; we can't always tell home/away

            # Try to link to a Game record
            game_id = None
            try:
                game_id = self._try_link_game(m, sport)
            except Exception as exc:
                logger.debug("Game link failed for %s: %s", ticker, exc)

            contract, was_created = MarketContract.objects.get_or_create(
                coinbase_product_id=external_id,
                defaults={
                    "source": MarketSource.KALSHI,
                    "sport": sport,
                    "title": title,
                    "description": m.get("rules_primary", "")[:500],
                    "contract_type": contract_type,
                    "game_id": game_id,
                    "game_date": game_date,
                    "expiry": expiry,
                    "is_active": True,
                    "is_resolved": False,
                },
            )

            # If we now have a game link but the record didn't before, update it
            if not was_created and game_id and not contract.game_id:
                contract.game_id = game_id
                contract.save(update_fields=["game_id"])

            yes_price, no_price, mid_price = _mid_price_from_market(m)
            vol = m.get("volume_24h_fp") or m.get("volume_fp")
            oi = m.get("open_interest_fp")

            MarketPrice.objects.create(
                contract=contract,
                yes_price=yes_price,
                no_price=no_price,
                mid_price=mid_price,
                volume_24h=float(vol) if vol else None,
                open_interest=float(oi) if oi else None,
            )

            if was_created:
                created += 1
                logger.info("Created: %s (%s)%s", ticker, sport, f" → game {game_id}" if game_id else "")
            else:
                updated += 1

        logger.info(
            "discover_and_create_contracts: created=%d updated=%d skipped=%d",
            created, updated, skipped,
        )
        return {"created": created, "updated": updated, "skipped": skipped}
