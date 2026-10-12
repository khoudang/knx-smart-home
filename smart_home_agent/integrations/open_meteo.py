from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class GeoLocation:
    name: str
    country: str
    latitude: float
    longitude: float
    admin1: str | None = None

    @property
    def label(self) -> str:
        parts = [self.name, self.admin1, self.country]
        return ", ".join(part for part in parts if part)


class OpenMeteoError(Exception):
    pass


class LocationNotFound(OpenMeteoError):
    pass


class OpenMeteoClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        retries: int = 1,
        geocode_ttl_seconds: int = 24 * 60 * 60,
        weather_ttl_seconds: int = 600,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.geocode_ttl_seconds = geocode_ttl_seconds
        self.weather_ttl_seconds = weather_ttl_seconds
        self._geocode_cache: dict[str, tuple[float, GeoLocation]] = {}
        self._weather_cache: dict[tuple[float, float, int], tuple[float, dict[str, Any]]] = {}

    def geocode(self, query: str) -> GeoLocation:
        cleaned = _normalize_location_query(query)
        if not cleaned:
            raise LocationNotFound("missing location")
        cached = self._geocode_cache.get(cleaned)
        now = time.time()
        if cached and now - cached[0] < self.geocode_ttl_seconds:
            return cached[1]
        params = {
            "name": cleaned,
            "count": "1",
            "language": "vi",
            "format": "json",
        }
        data = self._get_json(GEOCODING_URL, params)
        results = data.get("results") or []
        if not results:
            raise LocationNotFound(cleaned)
        first = results[0]
        location = GeoLocation(
            name=str(first.get("name") or cleaned),
            country=str(first.get("country") or ""),
            admin1=first.get("admin1"),
            latitude=float(first["latitude"]),
            longitude=float(first["longitude"]),
        )
        self._geocode_cache[cleaned] = (now, location)
        return location

    def forecast(self, location: GeoLocation, forecast_days: int = 2) -> dict[str, Any]:
        days = max(1, min(int(forecast_days or 2), 7))
        key = (round(location.latitude, 4), round(location.longitude, 4), days)
        cached = self._weather_cache.get(key)
        now = time.time()
        if cached and now - cached[0] < self.weather_ttl_seconds:
            return cached[1]
        params = {
            "latitude": str(location.latitude),
            "longitude": str(location.longitude),
            "timezone": "Asia/Ho_Chi_Minh",
            "forecast_days": str(days),
            "current": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "precipitation",
                    "rain",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "hourly": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "precipitation_probability",
                    "precipitation",
                    "rain",
                    "weather_code",
                ]
            ),
            "daily": ",".join(
                [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_sum",
                    "precipitation_probability_max",
                ]
            ),
        }
        data = self._get_json(FORECAST_URL, params)
        self._weather_cache[key] = (now, data)
        return data

    def weather(self, query: str, forecast_days: int = 2) -> tuple[GeoLocation, dict[str, Any]]:
        location = self.geocode(query)
        return location, self.forecast(location, forecast_days=forecast_days)

    def _get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        last_error: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                request = urllib.request.Request(full_url, headers={"User-Agent": "smart-home-agent/1.0"})
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
        raise OpenMeteoError(str(last_error))


def _normalize_location_query(query: str) -> str:
    cleaned = " ".join(str(query or "").strip().split())
    aliases = {
        "tp.hcm": "Ho Chi Minh City",
        "tphcm": "Ho Chi Minh City",
        "tp hcm": "Ho Chi Minh City",
        "hcm": "Ho Chi Minh City",
        "sai gon": "Ho Chi Minh City",
        "sài gòn": "Ho Chi Minh City",
    }
    return aliases.get(cleaned.lower(), cleaned)
