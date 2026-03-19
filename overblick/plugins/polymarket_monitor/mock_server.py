"""
Polymarket Mock Server — Simulerad API för testning.

Ger en fullt fungerande Polymarket-API-mock som returnerar realistiska
marknadsdata utan att behöva verkliga API-nycklar eller konto.
"""

import asyncio
import json
import random
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Dict, List, Any


# Simulerade marknader för olika kategorier
MARKETS_DATA = [
    {
        "id": "poly_test_001",
        "slug": "will-bitcoin-hit-100k-by-end-2026",
        "question": "Will Bitcoin hit $100,000 by end of 2026?",
        "description": "This market will resolve to Yes if Bitcoin (BTC) reaches or exceeds $100,000 USD at any point between now and December 31, 2026.",
        "category": "crypto",
        "status": "open",
        "createdTime": datetime.now().isoformat() + "Z",
        "endTime": (datetime.now() + timedelta(days=365)).isoformat() + "Z",
        "outcomes": [
            {"name": "Yes", "ticker": "YES", "price": 0.42, "volume24h": 125000, "lastUpdated": datetime.now().isoformat() + "Z"},
            {"name": "No", "ticker": "NO", "price": 0.58, "volume24h": 98000, "lastUpdated": datetime.now().isoformat() + "Z"}
        ],
        "volume24h": 223000,
        "liquidity": 156000,
        "openInterest": 187000,
    },
    {
        "id": "poly_test_002",
        "slug": "will-trump-win-2024-election",
        "question": "Will Donald Trump win the 2024 US Presidential Election?",
        "description": "This market will resolve to Yes if Donald Trump wins the 2024 United States presidential election.",
        "category": "politics",
        "status": "open",
        "createdTime": datetime.now().isoformat() + "Z",
        "endTime": (datetime.now() + timedelta(days=30)).isoformat() + "Z",
        "outcomes": [
            {"name": "Yes", "ticker": "YES", "price": 0.51, "volume24h": 890000, "lastUpdated": datetime.now().isoformat() + "Z"},
            {"name": "No", "ticker": "NO", "price": 0.49, "volume24h": 756000, "lastUpdated": datetime.now().isoformat() + "Z"}
        ],
        "volume24h": 1646000,
        "liquidity": 2100000,
        "openInterest": 1890000,
    },
    {
        "id": "poly_test_003",
        "slug": "will-ethereum-hit-5k-by-q2-2026",
        "question": "Will Ethereum hit $5,000 by Q2 2026?",
        "description": "This market resolves to Yes if ETH reaches or exceeds $5,000 USD before June 30, 2026.",
        "category": "crypto",
        "status": "open",
        "createdTime": datetime.now().isoformat() + "Z",
        "endTime": (datetime.now() + timedelta(days=400)).isoformat() + "Z",
        "outcomes": [
            {"name": "Yes", "ticker": "YES", "price": 0.28, "volume24h": 67000, "lastUpdated": datetime.now().isoformat() + "Z"},
            {"name": "No", "ticker": "NO", "price": 0.72, "volume24h": 54000, "lastUpdated": datetime.now().isoformat() + "Z"}
        ],
        "volume24h": 121000,
        "liquidity": 89000,
        "openInterest": 103000,
    },
    {
        "id": "poly_test_004",
        "slug": "will-switzerland-join-nato-by-2027",
        "question": "Will Switzerland join NATO by end of 2027?",
        "description": "This market resolves to Yes if Switzerland officially becomes a member of NATO before January 1, 2028.",
        "category": "politics",
        "status": "open",
        "createdTime": datetime.now().isoformat() + "Z",
        "endTime": (datetime.now() + timedelta(days=730)).isoformat() + "Z",
        "outcomes": [
            {"name": "Yes", "ticker": "YES", "price": 0.15, "volume24h": 23000, "lastUpdated": datetime.now().isoformat() + "Z"},
            {"name": "No", "ticker": "NO", "price": 0.85, "volume24h": 19000, "lastUpdated": datetime.now().isoformat() + "Z"}
        ],
        "volume24h": 42000,
        "liquidity": 35000,
        "openInterest": 38000,
    },
    {
        "id": "poly_test_005",
        "slug": "will-sp500-reach-6000-by-december-2026",
        "question": "Will S&P 500 reach 6,000 by December 2026?",
        "description": "This market resolves to Yes if the S&P 500 index closes at or above 6,000 on any trading day before end of 2026.",
        "category": "finance",
        "status": "open",
        "createdTime": datetime.now().isoformat() + "Z",
        "endTime": (datetime.now() + timedelta(days=650)).isoformat() + "Z",
        "outcomes": [
            {"name": "Yes", "ticker": "YES", "price": 0.38, "volume24h": 178000, "lastUpdated": datetime.now().isoformat() + "Z"},
            {"name": "No", "ticker": "NO", "price": 0.62, "volume24h": 145000, "lastUpdated": datetime.now().isoformat() + "Z"}
        ],
        "volume24h": 323000,
        "liquidity": 287000,
        "openInterest": 305000,
    },
]


class MockPolymarketHandler(BaseHTTPRequestHandler):
    """HTTP handler for mock Polymarket API."""
    
    def log_message(self, format, *args):
        """Suppress default logging."""
        pass
    
    def send_json_response(self, data: Any, status: int = 200):
        """Send JSON response with proper headers."""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def do_GET(self):
        """Handle GET requests."""
        # Parse path and query params
        from urllib.parse import urlparse, parse_qs
        
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        
        if path == "/markets":
            self.handle_markets(params)
        elif path.startswith("/markets/"):
            market_id = path.split("/")[-1]
            self.handle_market_by_id(market_id)
        elif path.startswith("/markets/slug/"):
            slug = path.replace("/markets/slug/", "")
            self.handle_market_by_slug(slug)
        elif path.endswith("/ticker"):
            # Extract market ID from /markets/{id}/ticker
            parts = path.split("/")
            if len(parts) >= 3:
                market_id = parts[-2]
                self.handle_ticker(market_id)
            else:
                self.send_json_response({"error": "Invalid ticker endpoint"}, 400)
        else:
            self.send_json_response({"error": "Not found"}, 404)
    
    def handle_markets(self, params: Dict):
        """Handle /markets endpoint."""
        limit = int(params.get("limit", [100])[0])
        offset = int(params.get("offset", [0])[0])
        
        # Simulate some rate limiting delay
        time.sleep(0.05)
        
        markets = MARKETS_DATA[offset:offset + limit]
        
        response = {
            "markets": markets,
            "total": len(MARKETS_DATA),
            "limit": limit,
            "offset": offset
        }
        
        self.send_json_response(response)
    
    def handle_market_by_id(self, market_id: str):
        """Handle /markets/{id} endpoint."""
        time.sleep(0.02)
        
        for market in MARKETS_DATA:
            if market["id"] == market_id:
                self.send_json_response(market)
                return
        
        self.send_json_response({"error": "Market not found"}, 404)
    
    def handle_market_by_slug(self, slug: str):
        """Handle /markets/slug/{slug} endpoint."""
        time.sleep(0.02)
        
        for market in MARKETS_DATA:
            if market["slug"] == slug or slug in market["slug"]:
                self.send_json_response(market)
                return
        
        self.send_json_response({"error": "Market not found"}, 404)
    
    def handle_ticker(self, market_id: str):
        """Handle /markets/{id}/ticker endpoint."""
        for market in MARKETS_DATA:
            if market["id"] == market_id:
                # Get current prices (slightly randomized to simulate live data)
                outcomes = []
                for outcome in market["outcomes"]:
                    # Add small random fluctuation
                    price_change = random.uniform(-0.02, 0.02)
                    new_price = max(0.01, min(0.99, outcome["price"] + price_change))
                    
                    outcomes.append({
                        "name": outcome["name"],
                        "ticker": outcome["ticker"],
                        "price": round(new_price, 4),
                        "volume24h": outcome["volume24h"],
                        "lastUpdated": datetime.now().isoformat() + "Z"
                    })
                
                response = {
                    "marketId": market_id,
                    "outcomes": outcomes,
                    "volume24h": market["volume24h"],
                    "liquidity": market["liquidity"],
                    "openInterest": market["openInterest"]
                }
                self.send_json_response(response)
                return
        
        self.send_json_response({"error": "Market not found"}, 404)


def run_mock_server(port: int = 8201):
    """Start the mock Polymarket server."""
    server = HTTPServer(('127.0.0.1', port), MockPolymarketHandler)
    print(f"🎭 Mock Polymarket Server running on http://127.0.0.1:{port}")
    print("Available endpoints:")
    print("  GET /markets?limit=50&offset=0 - List all markets")
    print("  GET /markets/{id} - Get market by ID")
    print("  GET /markets/slug/{slug} - Get market by URL slug")
    print("  GET /markets/{id}/ticker - Get real-time ticker data")
    server.serve_forever()


def start_mock_server(port: int = 8201) -> Thread:
    """Start mock server in background thread."""
    thread = Thread(target=run_mock_server, args=(port,), daemon=True)
    thread.start()
    time.sleep(0.5)  # Wait for server to start
    return thread


if __name__ == "__main__":
    import sys
    
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8201
    run_mock_server(port)
