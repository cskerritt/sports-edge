"""
Coinbase Advanced Trade API client for prediction market contracts.

API docs: https://docs.cdp.coinbase.com/advanced-trade/reference/
Prediction market products are identified by their product_id and have
product_type='FUTURE' (or similar). Binary YES/NO contracts trade between
$0 and $1, settling at $1 if the event occurs.
"""

import hashlib
import hmac
import logging
import time

import requests
from django.conf import settings

from markets.models import MarketContract, MarketPrice

logger = logging.getLogger("markets.coinbase")


class CoinbasePredictionClient:
    """
    Client for Coinbase Advanced Trade API prediction market contracts.

    Handles HMAC-SHA256 request signing, product discovery, and price
    fetching. Gracefully degrades when API credentials are absent — all
    methods return empty structures and log a warning rather than raising.
    """

    BASE_URL = "https://api.coinbase.com"

    def __init__(self):
        self.api_key = getattr(settings, "COINBASE_API_KEY", "")
        self.api_secret = getattr(settings, "COINBASE_API_SECRET", "")
        self.session = requests.Session()
        self.logger = logging.getLogger("markets.coinbase")

        if not self.api_key or not self.api_secret:
            self.logger.warning(
                "COINBASE_API_KEY or COINBASE_API_SECRET is not configured. "
                "All Coinbase API calls will return empty results."
            )

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    @property
    def _has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _sign_request(self, method: str, path: str, body: str = "") -> dict:
        """Return signed headers for an authenticated request.

        Signature: HMAC-SHA256 over ``timestamp + METHOD + path + body``.
        """
        timestamp = str(int(time.time()))
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
        return {
            "CB-ACCESS-KEY": self.api_key,
            "CB-ACCESS-SIGN": signature,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict | list | None:
        """Perform a signed GET request and return the parsed JSON body.

        Returns ``None`` on any error so callers can handle gracefully.
        """
        if not self._has_credentials:
            return None

        url = self.BASE_URL + path
        headers = self._sign_request("GET", path)

        try:
            resp = self.session.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            self.logger.error("Coinbase HTTP error %s for %s: %s", exc.response.status_code, path, exc)
        except requests.exceptions.RequestException as exc:
            self.logger.error("Coinbase request error for %s: %s", path, exc)
        except ValueError as exc:
            self.logger.error("Coinbase JSON decode error for %s: %s", path, exc)

        return None

    # ------------------------------------------------------------------
    # Product discovery
    # ------------------------------------------------------------------

    def get_prediction_products(self) -> list[dict]:
        """Fetch all active prediction market products.

        Calls ``GET /api/v3/brokerage/market/products`` with
        ``product_type=FUTURE`` and a generous limit, then keeps only
        products whose ``product_id`` or ``display_name`` looks like a
        binary YES/NO prediction contract (e.g. ends in ``-YES-USD`` or
        contains ``WILL``).

        Returns a list of product dicts with keys:
            ``product_id``, ``display_name``, ``base_name``, ``quote_name``,
            ``price``, ``price_percentage_change_24h``, ``volume_24h``,
            ``status``.
        """
        if not self._has_credentials:
            return []

        path = "/api/v3/brokerage/market/products"
        data = self._get(path, params={"product_type": "FUTURE", "limit": 250})
        if not data:
            return []

        raw_products: list[dict] = data.get("products", [])

        # Filter to prediction-market-style contracts.
        # Coinbase prediction market product_ids typically look like:
        #   SUPER-BOWL-LIX-KC-YES-USD  /  NBA-FINALS-2025-BOS-NO-USD
        # We keep anything whose id ends in -USD and contains -YES- or -NO-,
        # or whose display_name contains prediction-market keywords.
        results: list[dict] = []
        for p in raw_products:
            pid: str = p.get("product_id", "")
            display: str = p.get("display_name", "")
            is_prediction = (
                ("-YES-" in pid or "-NO-" in pid or "WILL-" in pid.upper())
                or ("YES" in display.upper() or "NO " in display.upper() or "WILL" in display.upper())
            )
            if not is_prediction:
                continue

            results.append({
                "product_id": pid,
                "display_name": display,
                "base_name": p.get("base_name", ""),
                "quote_name": p.get("quote_name", "USD"),
                "price": p.get("price", "0"),
                "price_percentage_change_24h": p.get("price_percentage_change_24h", "0"),
                "volume_24h": p.get("volume_24h", "0"),
                "status": p.get("status", "unknown"),
            })

        self.logger.info("Fetched %d prediction products from Coinbase.", len(results))
        return results

    # ------------------------------------------------------------------
    # Order book / price data
    # ------------------------------------------------------------------

    def get_product_book(self, product_id: str) -> dict:
        """Get the best bid/ask order book for a contract.

        ``GET /api/v3/brokerage/market/product_book?product_id={id}&limit=1``

        Returns a dict::

            {
                "pricebook": {
                    "product_id": "...",
                    "bids": [{"price": "0.65", "size": "10"}],
                    "asks": [{"price": "0.67", "size": "8"}],
                }
            }

        Returns an empty dict on failure.
        """
        if not self._has_credentials:
            return {}

        path = "/api/v3/brokerage/market/product_book"
        data = self._get(path, params={"product_id": product_id, "limit": 1})
        return data or {}

    def get_best_bid_ask(self, product_id: str) -> dict:
        """Fetch the best bid and ask prices for one or more product IDs.

        ``GET /api/v3/brokerage/market/best_bid_ask?product_ids={id}``

        Returns the raw API response dict, or ``{}`` on failure.
        Example response::

            {
                "pricebooks": [
                    {
                        "product_id": "SUPER-BOWL-KC-YES-USD",
                        "bids": [{"price": "0.64", "size": "25"}],
                        "asks": [{"price": "0.66", "size": "15"}],
                        "time": "2025-01-01T00:00:00Z"
                    }
                ]
            }
        """
        if not self._has_credentials:
            return {}

        path = "/api/v3/brokerage/market/best_bid_ask"
        data = self._get(path, params={"product_ids": product_id})
        return data or {}

    # ------------------------------------------------------------------
    # Price parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_price_to_probability(price_str: str) -> float:
        """Convert a price string such as ``"0.65"`` to a float probability.

        Coinbase prediction markets price YES shares between 0 and 1; this
        price is already the implied probability. Values outside ``[0.0, 1.0]``
        are clamped.
        """
        try:
            p = float(price_str)
        except (TypeError, ValueError):
            return 0.5  # neutral fallback
        return max(0.0, min(p, 1.0))

    def _extract_best_prices(self, bba_response: dict, product_id: str) -> tuple[float | None, float | None]:
        """Extract best bid and best ask from a best_bid_ask response.

        Returns ``(best_bid, best_ask)`` as floats, or ``None`` if unavailable.
        """
        pricebooks: list[dict] = bba_response.get("pricebooks", [])
        for pb in pricebooks:
            if pb.get("product_id") == product_id:
                bids = pb.get("bids", [])
                asks = pb.get("asks", [])
                best_bid = float(bids[0]["price"]) if bids else None
                best_ask = float(asks[0]["price"]) if asks else None
                return best_bid, best_ask
        return None, None

    # ------------------------------------------------------------------
    # Main fetch-and-store
    # ------------------------------------------------------------------

    def fetch_and_store_prices(self) -> dict:
        """Fetch current prices for all active MarketContracts and persist them.

        Workflow:
        1. Load all active, non-resolved ``MarketContract`` records.
        2. For each contract call ``get_best_bid_ask`` using its
           ``coinbase_product_id``.
        3. Parse bid/ask into YES/NO probabilities and compute a mid-price.
        4. Create a new ``MarketPrice`` record.

        Returns a summary dict::

            {"fetched": int, "errors": int}
        """
        if not self._has_credentials:
            self.logger.warning("fetch_and_store_prices: no credentials — skipping.")
            return {"fetched": 0, "errors": 0}

        contracts = MarketContract.objects.filter(is_active=True, is_resolved=False)
        fetched = 0
        errors = 0

        for contract in contracts:
            try:
                response = self.get_best_bid_ask(contract.coinbase_product_id)
                best_bid, best_ask = self._extract_best_prices(response, contract.coinbase_product_id)

                if best_bid is None and best_ask is None:
                    self.logger.debug(
                        "No bid/ask data for %s — skipping.", contract.coinbase_product_id
                    )
                    errors += 1
                    continue

                # YES price = mid of bid and ask (or whichever is available)
                if best_bid is not None and best_ask is not None:
                    yes_price = (best_bid + best_ask) / 2.0
                elif best_bid is not None:
                    yes_price = best_bid
                else:
                    yes_price = best_ask  # type: ignore[assignment]

                yes_price = max(0.01, min(yes_price, 0.99))
                no_price = max(0.01, min(1.0 - yes_price, 0.99))

                # Spread-adjusted mid: average of YES bid and NO bid
                # (NO bid = 1 - YES ask)
                if best_bid is not None and best_ask is not None:
                    no_bid = 1.0 - best_ask
                    mid_price = (best_bid + no_bid) / 2.0
                else:
                    mid_price = yes_price

                mid_price = max(0.01, min(mid_price, 0.99))

                MarketPrice.objects.create(
                    contract=contract,
                    yes_price=yes_price,
                    no_price=no_price,
                    mid_price=mid_price,
                )
                fetched += 1

            except Exception as exc:  # noqa: BLE001
                self.logger.error(
                    "Error fetching price for contract %s (%s): %s",
                    contract.pk,
                    contract.coinbase_product_id,
                    exc,
                )
                errors += 1

        self.logger.info(
            "fetch_and_store_prices complete: fetched=%d errors=%d", fetched, errors
        )
        return {"fetched": fetched, "errors": errors}

    # ------------------------------------------------------------------
    # Game-specific search
    # ------------------------------------------------------------------

    def search_contracts_for_game(self, home_team: str, away_team: str, sport: str) -> list[dict]:
        """Search Coinbase prediction products for contracts related to a game.

        Filters ``get_prediction_products()`` by checking whether either team
        name (case-insensitive) appears in the product's ``display_name``.

        Parameters
        ----------
        home_team, away_team:
            Team names or abbreviations to search for.
        sport:
            Sport code (e.g. ``"NFL"``); used for logging only.

        Returns
        -------
        List of matching product dicts (same shape as ``get_prediction_products``).
        """
        products = self.get_prediction_products()
        if not products:
            return []

        home_lower = home_team.lower()
        away_lower = away_team.lower()

        matches = [
            p for p in products
            if home_lower in p["display_name"].lower()
            or away_lower in p["display_name"].lower()
        ]

        self.logger.debug(
            "search_contracts_for_game(%s vs %s, %s): found %d contract(s).",
            away_team,
            home_team,
            sport,
            len(matches),
        )
        return matches
