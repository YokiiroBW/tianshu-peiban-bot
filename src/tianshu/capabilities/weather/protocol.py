"""QWeather host and Geo parsing reused from the effective platform adapter."""

import re
from urllib.parse import urlsplit
from ..contracts import require, Fault


def host_url(value):
    require(isinstance(value, str) and len(value) <= 253, "invalid_input", 400)
    value = value.strip().rstrip("/")
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise Fault("invalid_weather_host", 400) from None
    require(
        parsed.scheme == "https"
        and re.fullmatch(
            r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+qweatherapi\.com",
            parsed.netloc.lower(),
        )
        and not parsed.path
        and not parsed.query
        and not parsed.fragment,
        "invalid_weather_host",
        400,
    )
    return "https://" + parsed.netloc.lower()


def location_view(row):
    require(isinstance(row, dict), "weather_invalid_response", 502)
    keys = {
        "id": "id",
        "name": "name",
        "adm1": "adm1",
        "adm2": "adm2",
        "country": "country",
        "tz": "tz",
        "utc_offset": "utcOffset",
    }
    result = {}
    for target, source in keys.items():
        value = row.get(source)
        require(
            isinstance(value, str) and len(value) <= 128 and (value == "" or value.isprintable()),
            "weather_invalid_response",
            502,
        )
        result[target] = value
    require(
        re.fullmatch(r"[A-Za-z0-9_-]{1,64}", result["id"])
        and re.fullmatch(r"[A-Za-z0-9_+/-]{1,128}", result["tz"])
        and re.fullmatch(r"[+-]\d{2}:\d{2}", result["utc_offset"]),
        "weather_invalid_response",
        502,
    )
    try:
        latitude, longitude = float(row["lat"]), float(row["lon"])
        require(-90 <= latitude <= 90 and -180 <= longitude <= 180, "weather_invalid_response", 502)
    except (KeyError, TypeError, ValueError):
        raise Fault("weather_invalid_response", 502) from None
    result.update(latitude=latitude, longitude=longitude)
    return result


def current_view(answer):
    weather = {"observed_at": None}
    for target, field in {"temp": "temperature", "feels_like": "feelsLike"}.items():
        item = answer.get(field)
        require(
            isinstance(item, dict)
            and item.get("unit") == "°C"
            and type(item.get("value")) in {int, float}
            and -150 <= item["value"] <= 100,
            "weather_invalid_response",
            502,
        )
        weather[target] = f"{item['value']:g}"
    condition, wind = answer.get("condition"), answer.get("wind")
    require(isinstance(condition, dict) and isinstance(wind, dict), "weather_invalid_response", 502)
    for target, field in {"text": "text", "icon": "code"}.items():
        item = condition.get(field)
        require(
            isinstance(item, str) and 1 <= len(item) <= 100 and item.isprintable(),
            "weather_invalid_response",
            502,
        )
        weather[target] = item
    require(
        type(wind.get("scale")) in {int, float} and 0 <= wind["scale"] <= 17,
        "weather_invalid_response",
        502,
    )
    weather["wind_scale"] = f"{wind['scale']:g}"
    attributions = (answer.get("metadata") or {}).get("attributions")
    require(
        isinstance(attributions, list)
        and len(attributions) <= 20
        and all(
            isinstance(item, str) and 1 <= len(item) <= 2048 and item.isprintable()
            for item in attributions
        ),
        "weather_invalid_response",
        502,
    )
    weather["attributions"] = attributions
    return weather
