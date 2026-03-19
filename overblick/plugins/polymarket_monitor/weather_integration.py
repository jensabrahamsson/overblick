"""
Weather data integration for Polymarket weather prediction markets.

Fetches real weather forecasts from various APIs and converts them to
probabilities for trading on Polymarket weather markets.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class WeatherAPIError(Exception):
    """Base exception for weather API errors."""

    pass


class WeatherForecast:
    """Represents a weather forecast with probabilities."""

    def __init__(
        self,
        location: str,
        forecast_date: datetime,
        temperature_c: Optional[float] = None,
        precipitation_mm: Optional[float] = None,
        precipitation_probability: Optional[float] = None,
        condition: Optional[str] = None,
        source: str = "unknown",
    ):
        self.location = location
        self.forecast_date = forecast_date
        self.temperature_c = temperature_c
        self.precipitation_mm = precipitation_mm
        self.precipitation_probability = precipitation_probability
        self.condition = condition
        self.source = source
        self.calculated_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "location": self.location,
            "forecast_date": self.forecast_date.isoformat(),
            "temperature_c": self.temperature_c,
            "precipitation_mm": self.precipitation_mm,
            "precipitation_probability": self.precipitation_probability,
            "condition": self.condition,
            "source": self.source,
            "calculated_at": self.calculated_at.isoformat(),
        }

    def get_rain_probability(self) -> float:
        """Get probability of rain (0-1)."""
        if self.precipitation_probability is not None:
            return self.precipitation_probability / 100.0

        # Estimate from precipitation amount
        if self.precipitation_mm is not None:
            if self.precipitation_mm > 5.0:
                return 0.9
            elif self.precipitation_mm > 1.0:
                return 0.7
            elif self.precipitation_mm > 0.1:
                return 0.4
            else:
                return 0.1

        # Estimate from condition
        if self.condition:
            condition_lower = self.condition.lower()
            if any(word in condition_lower for word in ["rain", "drizzle", "shower"]):
                return 0.8
            elif any(word in condition_lower for word in ["storm", "thunder"]):
                return 0.9
            elif "cloud" in condition_lower:
                return 0.3
            elif "clear" in condition_lower or "sun" in condition_lower:
                return 0.1

        return 0.5  # Default

    def get_temperature_exceed_probability(self, threshold_c: float) -> float:
        """Get probability that temperature will exceed threshold (0-1)."""
        if self.temperature_c is None:
            return 0.5

        # Simple model: assume normal distribution around forecast
        # with standard deviation based on forecast horizon
        days_ahead = (self.forecast_date.date() - datetime.now().date()).days
        std_dev = 2.0 + (days_ahead * 1.5)  # More uncertainty further out

        # Z-score for threshold
        z = (threshold_c - self.temperature_c) / std_dev

        # Probability from normal CDF (simplified)
        if z <= -2:
            return 0.95
        elif z <= -1:
            return 0.85
        elif z <= 0:
            return 0.70
        elif z <= 1:
            return 0.30
        elif z <= 2:
            return 0.15
        else:
            return 0.05

    def get_snow_probability(self) -> float:
        """Get probability of snow (0-1)."""
        if self.temperature_c is not None and self.temperature_c > 2.0:
            return 0.0  # Too warm for snow

        if self.condition and "snow" in self.condition.lower():
            return 0.8

        rain_prob = self.get_rain_probability()
        if self.temperature_c is not None and self.temperature_c < 0:
            return rain_prob * 0.9  # Most precipitation is snow

        return rain_prob * 0.1  # Some chance of snow


class OpenWeatherMapClient:
    """Client for OpenWeatherMap API (free tier)."""

    BASE_URL = "https://api.openweathermap.org/data/2.5"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    async def get_forecast(self, city: str, country_code: str = "SE") -> List[WeatherForecast]:
        """Get 5-day forecast for a city."""
        import aiohttp

        if not self.api_key:
            logger.warning("OpenWeatherMap API key not set, using mock data")
            return self._get_mock_forecast(city)

        # Geocoding to get coordinates
        geocode_url = f"http://api.openweathermap.org/geo/1.0/direct"

        async with aiohttp.ClientSession() as session:
            try:
                # Get coordinates
                params = {
                    "q": f"{city},{country_code}",
                    "limit": 1,
                    "appid": self.api_key,
                }

                async with session.get(geocode_url, params=params) as response:
                    if response.status != 200:
                        raise WeatherAPIError(f"Geocoding failed: {response.status}")

                    geocode_data = await response.json()
                    if not geocode_data:
                        raise WeatherAPIError(f"City not found: {city}")

                    lat = geocode_data[0]["lat"]
                    lon = geocode_data[0]["lon"]

                # Get forecast
                forecast_url = f"{self.BASE_URL}/forecast"
                params = {
                    "lat": lat,
                    "lon": lon,
                    "appid": self.api_key,
                    "units": "metric",  # Celsius
                }

                async with session.get(forecast_url, params=params) as response:
                    if response.status != 200:
                        raise WeatherAPIError(f"Forecast failed: {response.status}")

                    forecast_data = await response.json()

                # Parse forecast
                forecasts = []
                for item in forecast_data.get("list", []):
                    forecast_time = datetime.fromtimestamp(item["dt"])

                    # Only take one forecast per day (12:00)
                    if forecast_time.hour == 12:
                        forecast = WeatherForecast(
                            location=city,
                            forecast_date=forecast_time,
                            temperature_c=item["main"]["temp"],
                            precipitation_mm=item.get("rain", {}).get("3h", 0),
                            condition=item["weather"][0]["description"],
                            source="openweathermap",
                        )
                        forecasts.append(forecast)

                logger.info(f"Got {len(forecasts)} forecasts for {city}")
                return forecasts

            except Exception as e:
                logger.error(f"OpenWeatherMap API error: {e}")
                return self._get_mock_forecast(city)

    def _get_mock_forecast(self, city: str) -> List[WeatherForecast]:
        """Generate mock forecast for testing."""
        forecasts = []
        now = datetime.now()

        # Common weather patterns by city
        city_patterns = {
            "stockholm": {"base_temp": 15, "rain_chance": 0.3},
            "london": {"base_temp": 12, "rain_chance": 0.5},
            "new york": {"base_temp": 18, "rain_chance": 0.4},
            "tokyo": {"base_temp": 20, "rain_chance": 0.4},
            "sydney": {"base_temp": 22, "rain_chance": 0.3},
        }

        pattern = city_patterns.get(city.lower(), {"base_temp": 15, "rain_chance": 0.3})

        for i in range(5):
            forecast_date = now + timedelta(days=i + 1)

            # Add some randomness
            import random

            temp_variation = random.uniform(-3, 3)
            temperature = pattern["base_temp"] + temp_variation

            rain_chance = pattern["rain_chance"] + random.uniform(-0.2, 0.2)
            rain_chance = max(0.0, min(1.0, rain_chance))

            precipitation = random.uniform(0, 5) if random.random() < rain_chance else 0

            conditions = ["Clear", "Partly cloudy", "Cloudy", "Light rain", "Rain"]
            condition = random.choices(conditions, weights=[0.3, 0.3, 0.2, 0.1, 0.1])[0]

            forecast = WeatherForecast(
                location=city,
                forecast_date=forecast_date,
                temperature_c=temperature,
                precipitation_mm=precipitation,
                precipitation_probability=rain_chance * 100,
                condition=condition,
                source="mock",
            )
            forecasts.append(forecast)

        logger.info(f"Generated {len(forecasts)} mock forecasts for {city}")
        return forecasts


class SMHIWrapper:
    """Wrapper for SMHI API (Swedish Meteorological and Hydrological Institute)."""

    BASE_URL = (
        "https://opendata-download-metfcst.smhi.se/api/category/pmp3g/version/2/geotype/point"
    )

    async def get_forecast(self, lat: float, lon: float) -> List[WeatherForecast]:
        """Get forecast from SMHI API."""
        import aiohttp

        url = f"{self.BASE_URL}/lon/{lon}/lat/{lat}/data.json"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise WeatherAPIError(f"SMHI API failed: {response.status}")

                    data = await response.json()

                # Parse SMHI forecast
                forecasts = []
                for time_series in data.get("timeSeries", []):
                    valid_time = datetime.fromisoformat(
                        time_series["validTime"].replace("Z", "+00:00")
                    )

                    # Extract parameters
                    temp = None
                    precipitation = None
                    condition = None

                    for param in time_series.get("parameters", []):
                        if param["name"] == "t":
                            temp = param["values"][0]  # Temperature in Celsius
                        elif param["name"] == "pmean":
                            precipitation = param["values"][0]  # Precipitation in mm/h
                        elif param["name"] == "wsymb2":
                            # Weather symbol - convert to condition
                            symbol = param["values"][0]
                            condition = self._symbol_to_condition(symbol)

                    # Only take one forecast per day (12:00)
                    if valid_time.hour == 12:
                        forecast = WeatherForecast(
                            location=f"{lat},{lon}",
                            forecast_date=valid_time,
                            temperature_c=temp,
                            precipitation_mm=precipitation,
                            condition=condition,
                            source="smhi",
                        )
                        forecasts.append(forecast)

                logger.info(f"Got {len(forecasts)} forecasts from SMHI")
                return forecasts

            except Exception as e:
                logger.error(f"SMHI API error: {e}")
                return []

    def _symbol_to_condition(self, symbol: int) -> str:
        """Convert SMHI weather symbol to condition string."""
        symbol_map = {
            1: "Clear sky",
            2: "Nearly clear sky",
            3: "Variable cloudiness",
            4: "Halfclear sky",
            5: "Cloudy sky",
            6: "Overcast",
            7: "Fog",
            8: "Light rain showers",
            9: "Moderate rain showers",
            10: "Heavy rain showers",
            11: "Thunderstorm",
            12: "Light sleet showers",
            13: "Moderate sleet showers",
            14: "Heavy sleet showers",
            15: "Light snow showers",
            16: "Moderate snow showers",
            17: "Heavy snow showers",
            18: "Light rain",
            19: "Moderate rain",
            20: "Heavy rain",
            21: "Thunder",
            22: "Light sleet",
            23: "Moderate sleet",
            24: "Heavy sleet",
            25: "Light snowfall",
            26: "Moderate snowfall",
            27: "Heavy snowfall",
        }
        return symbol_map.get(symbol, "Unknown")


class WeatherAnalyzer:
    """Main weather analysis for Polymarket trading."""

    def __init__(self, openweather_api_key: Optional[str] = None):
        self.openweather = OpenWeatherMapClient(openweather_api_key)
        self.smhi = SMHIWrapper()
        self.cache: Dict[str, Tuple[datetime, List[WeatherForecast]]] = {}
        self.cache_ttl = timedelta(hours=1)

    async def analyze_weather_market(self, market_question: str) -> Dict[str, Any]:
        """
        Analyze a weather prediction market.

        Args:
            market_question: e.g., "Will it rain in London tomorrow?"

        Returns:
            Analysis with probability estimate and confidence
        """
        # Parse market question
        parsed = self._parse_weather_question(market_question)
        if not parsed:
            return {"error": "Could not parse weather question"}

        location = parsed["location"]
        condition = parsed["condition"]
        threshold = parsed.get("threshold")
        date_str = parsed.get("date", "tomorrow")

        # Get forecast
        forecasts = await self._get_forecast_for_location(location)
        if not forecasts:
            return {"error": f"No forecast available for {location}"}

        # Find relevant forecast date
        target_date = self._parse_date_string(date_str)
        forecast = self._find_closest_forecast(forecasts, target_date)

        if not forecast:
            return {"error": f"No forecast for {date_str}"}

        # Calculate probability based on condition
        probability = self._calculate_probability(forecast, condition, threshold)
        confidence = self._calculate_confidence(forecast, condition)

        return {
            "location": location,
            "date": forecast.forecast_date.strftime("%Y-%m-%d"),
            "condition": condition,
            "threshold": threshold,
            "probability": probability,
            "confidence": confidence,
            "forecast_details": forecast.to_dict(),
            "source": forecast.source,
            "analyzed_at": datetime.now().isoformat(),
        }

    def _parse_weather_question(self, question: str) -> Optional[Dict[str, Any]]:
        """Parse weather market question."""
        question_lower = question.lower()

        # Common patterns
        patterns = [
            # "Will it rain in [location] [date]?"
            (r"will it rain in (.+?)\s+(tomorrow|today|next week|on \w+ \d+)", "rain", None),
            (r"will it rain in (.+?)\?", "rain", None),
            # "Will temperature exceed [threshold]°C in [location] [date]?"
            (
                r"will temperature exceed (\d+)°c in (.+?)\s+(tomorrow|today|next week|on \w+ \d+)",
                "temperature_exceed",
                None,
            ),
            (
                r"will it be above (\d+)°c in (.+?)\s+(tomorrow|today|next week|on \w+ \d+)",
                "temperature_exceed",
                None,
            ),
            # "Will it snow in [location] [date]?"
            (r"will it snow in (.+?)\s+(tomorrow|today|next week|on \w+ \d+)", "snow", None),
            # Generic
            (
                r"will there be (.+?) in (.+?)\s+(tomorrow|today|next week|on \w+ \d+)",
                "generic",
                None,
            ),
        ]

        import re

        for pattern, condition_type, _ in patterns:
            match = re.search(pattern, question_lower)
            if match:
                result = {"condition": condition_type}

                if condition_type == "temperature_exceed":
                    result["threshold"] = float(match.group(1))
                    result["location"] = match.group(2).strip()
                    if len(match.groups()) > 2:
                        result["date"] = match.group(3)
                else:
                    result["location"] = match.group(1).strip()
                    if len(match.groups()) > 1:
                        result["date"] = match.group(2)

                return result

        # Fallback: try to extract location
        common_cities = [
            "stockholm",
            "london",
            "new york",
            "tokyo",
            "sydney",
            "paris",
            "berlin",
            "moscow",
            "beijing",
            "mumbai",
        ]

        for city in common_cities:
            if city in question_lower:
                return {
                    "location": city.title(),
                    "condition": "unknown",
                    "date": "tomorrow",
                }

        return None

    def _parse_date_string(self, date_str: str) -> datetime:
        """Parse date string like 'tomorrow', 'next week'."""
        now = datetime.now()

        if date_str == "today":
            return now.replace(hour=12, minute=0, second=0, microsecond=0)
        elif date_str == "tomorrow":
            return (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        elif date_str == "next week":
            return (now + timedelta(days=7)).replace(hour=12, minute=0, second=0, microsecond=0)

        # Try to parse actual date
        try:
            from dateutil import parser

            return parser.parse(date_str).replace(hour=12, minute=0, second=0, microsecond=0)
        except:
            # Default to tomorrow
            return (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)

    async def _get_forecast_for_location(self, location: str) -> List[WeatherForecast]:
        """Get forecast for location, with caching."""
        cache_key = location.lower()

        # Check cache
        if cache_key in self.cache:
            cached_time, forecasts = self.cache[cache_key]
            if datetime.now() - cached_time < self.cache_ttl:
                logger.debug(f"Using cached forecast for {location}")
                return forecasts

        # Try OpenWeatherMap first
        forecasts = await self.openweather.get_forecast(location)

        # Cache results
        self.cache[cache_key] = (datetime.now(), forecasts)

        return forecasts

    def _find_closest_forecast(
        self, forecasts: List[WeatherForecast], target_date: datetime
    ) -> Optional[WeatherForecast]:
        """Find forecast closest to target date."""
        if not forecasts:
            return None

        # Find forecast with closest date
        closest = min(forecasts, key=lambda f: abs((f.forecast_date - target_date).total_seconds()))

        # If more than 24 hours difference, might not be relevant
        if abs((closest.forecast_date - target_date).total_seconds()) > 24 * 3600:
            logger.warning(f"No close forecast for {target_date}, using {closest.forecast_date}")

        return closest

    def _calculate_probability(
        self, forecast: WeatherForecast, condition: str, threshold: Optional[float] = None
    ) -> float:
        """Calculate probability for weather condition."""
        if condition == "rain":
            return forecast.get_rain_probability()
        elif condition == "snow":
            return forecast.get_snow_probability()
        elif condition == "temperature_exceed" and threshold is not None:
            return forecast.get_temperature_exceed_probability(threshold)
        else:
            # Unknown condition, use 50% as default
            return 0.5

    def _calculate_confidence(self, forecast: WeatherForecast, condition: str) -> float:
        """Calculate confidence in probability estimate (0-100)."""
        base_confidence = 80.0  # Base confidence for weather forecasts

        # Adjust based on forecast source
        if forecast.source == "mock":
            base_confidence *= 0.5  # Less confidence in mock data

        # Adjust based on forecast horizon
        days_ahead = (forecast.forecast_date.date() - datetime.now().date()).days
        horizon_factor = max(0.3, 1.0 - (days_ahead * 0.15))  # Less confidence further out
        base_confidence *= horizon_factor

        # Adjust based on condition type
        if condition == "temperature_exceed":
            # Temperature forecasts are generally reliable
            base_confidence *= 1.1
        elif condition in ["rain", "snow"]:
            # Precipitation forecasts have more uncertainty
            base_confidence *= 0.9

        return min(100.0, max(0.0, base_confidence))


# Example usage
async def test_weather_analysis():
    """Test weather analysis."""
    analyzer = WeatherAnalyzer()

    test_questions = [
        "Will it rain in London tomorrow?",
        "Will temperature exceed 25°C in Stockholm next week?",
        "Will it snow in New York tomorrow?",
    ]

    for question in test_questions:
        print(f"\nAnalyzing: {question}")
        analysis = await analyzer.analyze_weather_market(question)

        if "error" in analysis:
            print(f"  Error: {analysis['error']}")
        else:
            print(f"  Probability: {analysis['probability']:.1%}")
            print(f"  Confidence: {analysis['confidence']:.0f}%")
            print(f"  Location: {analysis['location']}")
            print(f"  Date: {analysis['date']}")
            print(f"  Source: {analysis['source']}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_weather_analysis())
