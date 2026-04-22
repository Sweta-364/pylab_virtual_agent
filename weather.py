import os

import requests


def Weather():
    city = os.getenv("WEATHER_CITY", "Patna").strip() or "Patna"
    url = f"https://wttr.in/{city}"

    try:
        response = requests.get(url, params={"format": "%t %C"}, timeout=10)
        response.raise_for_status()
        weather_text = response.text.strip()
        if not weather_text:
            return f"Unable to fetch weather for {city} right now."
        return f"{city}: {weather_text}"
    except Exception:
        return f"Unable to fetch weather for {city} right now."
