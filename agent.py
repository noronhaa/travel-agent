import os
import json
import requests
from datetime import datetime, date, timedelta
from typing import Optional
from tavily import TavilyClient

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o")

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 71: "Light snow", 73: "Snow",
    75: "Heavy snow", 80: "Rain showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 95: "Thunderstorm", 96: "Thunderstorm with hail",
}


def _llm(messages: list[dict], temperature: float = 0.3) -> str:
    import time
    last_err = None
    for attempt in range(3):
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": MODEL, "messages": messages, "temperature": temperature},
            timeout=120,
        )
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            wait = min(retry_after, 90)
            last_err = f"Rate limited (attempt {attempt + 1}/3). Waiting {wait}s…"
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    raise Exception(
        f"OpenRouter rate limit hit after 3 attempts. "
        "Add credits at openrouter.ai/settings/credits or wait a minute and try again."
    )


def _search(query: str, max_results: int = 5) -> list[dict]:
    client = TavilyClient(api_key=TAVILY_API_KEY)
    result = client.search(query=query, max_results=max_results)
    return result.get("results", [])


def _format_results(results: list[dict]) -> str:
    lines = []
    for r in results:
        lines.append(f"**{r.get('title', '')}**\n{r.get('content', '')}\nURL: {r.get('url', '')}")
    return "\n\n---\n\n".join(lines)


def _get_weather_forecast(destination: str, date_from: str, date_to: str) -> Optional[dict]:
    """Fetch actual weather forecast from Open-Meteo if dates are within 16 days."""
    today = date.today()
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)
    days_until = (d_from - today).days

    if days_until > 16 or d_from < today:
        return None

    # Clamp end date to forecast window
    max_end = today + timedelta(days=16)
    if d_to > max_end:
        d_to = max_end

    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": destination, "count": 1, "language": "en", "format": "json"},
            timeout=8,
        ).json()
        results = geo.get("results", [])
        if not results:
            return None

        loc = results[0]
        weather = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
                "start_date": date_from,
                "end_date": d_to.isoformat(),
                "timezone": "auto",
            },
            timeout=8,
        ).json()

        daily = weather.get("daily", {})
        days_data = []
        dates = daily.get("time", [])
        for i, d_str in enumerate(dates):
            code = daily.get("weathercode", [])[i] if i < len(daily.get("weathercode", [])) else 0
            days_data.append({
                "date": d_str,
                "max_c": round(daily.get("temperature_2m_max", [])[i], 1) if i < len(daily.get("temperature_2m_max", [])) else None,
                "min_c": round(daily.get("temperature_2m_min", [])[i], 1) if i < len(daily.get("temperature_2m_min", [])) else None,
                "precip_mm": round(daily.get("precipitation_sum", [])[i], 1) if i < len(daily.get("precipitation_sum", [])) else None,
                "description": WMO_CODES.get(code, "Unknown"),
            })

        return {
            "location": f"{loc.get('name')}, {loc.get('country', '')}",
            "days": days_data,
        }
    except Exception:
        return None


def _format_forecast(forecast: dict) -> str:
    lines = [f"### Actual Weather Forecast for {forecast['location']}\n"]
    lines.append("| Date | Condition | High | Low | Rain |")
    lines.append("|---|---|---|---|---|")
    for d in forecast["days"]:
        max_f = round(d['max_c'] * 9/5 + 32, 1) if d['max_c'] is not None else "?"
        min_f = round(d['min_c'] * 9/5 + 32, 1) if d['min_c'] is not None else "?"
        lines.append(
            f"| {d['date']} | {d['description']} | {d['max_c']}°C / {max_f}°F "
            f"| {d['min_c']}°C / {min_f}°F | {d['precip_mm']}mm |"
        )
    return "\n".join(lines)


def _time_label(params: dict) -> str:
    if params.get("date_from") and params.get("date_to"):
        return f"{params['date_from']} to {params['date_to']}"
    return params.get("month", "")


def _build_search_queries(params: dict) -> list[str]:
    dest = params["destination"]
    time_label = _time_label(params)
    month = params.get("month") or datetime.fromisoformat(params["date_from"]).strftime("%B") if params.get("date_from") else ""
    origin = params.get("traveling_from", "")
    interests = [i.strip() for i in params.get("interests", "").split(",") if i.strip()]

    queries = [
        f"{dest} top things to do must see attractions",
        f"{dest} travel guide {month} activities experiences",
        f"{dest} accommodation costs {month} budget mid-range prices",
        f"{dest} local currency cost of living daily budget tourist {month}",
    ]

    for interest in interests:
        queries.append(f"{dest} {interest} guide best spots tips {month}")

    if origin:
        if params.get("date_from"):
            queries.append(f"flights {origin} to {dest} {time_label} price book")
        else:
            queries.append(f"flights from {origin} to {dest} {month} price")
        queries.append(f"how to get from {origin} to {dest} transport options cost")

    return queries


def _gather_research(params: dict) -> str:
    queries = _build_search_queries(params)
    all_text = []

    for q in queries:
        try:
            results = _search(q, max_results=4)
            if results:
                all_text.append(f"### Search: {q}\n\n{_format_results(results)}")
        except Exception as e:
            all_text.append(f"### Search: {q}\n\n(Search failed: {e})")

    return "\n\n" + "=" * 60 + "\n\n".join(all_text)


def _build_brief_prompt(params: dict, research: str, forecast: Optional[dict]) -> str:
    dest = params["destination"]
    duration = params["duration"]
    origin = params.get("traveling_from", "")
    interests = [i.strip() for i in params.get("interests", "").split(",") if i.strip()]
    has_dates = bool(params.get("date_from") and params.get("date_to"))
    time_label = _time_label(params)
    month = params.get("month") or datetime.fromisoformat(params["date_from"]).strftime("%B") if params.get("date_from") else ""

    # ── Weather section instruction ──────────────────────────────
    if has_dates and forecast:
        weather_instruction = f"""## 🌤️ Weather Forecast
The ACTUAL weather forecast data is provided in the research section below. Use it directly:
- Show the day-by-day forecast table
- Summarise the overall conditions for the trip
- List what to pack based on these specific conditions
"""
    elif has_dates:
        weather_instruction = f"""## 🌤️ Weather ({time_label})
Specific dates given but forecast is beyond the 16-day window. Based on typical {month} conditions:
- 2–3 bullet points on typical temperatures and conditions for {dest} in {month}
- Brief packing list
"""
    else:
        weather_instruction = f"""## 🌤️ Weather in {month}
Month given (no specific dates). Keep this section brief:
- One sentence on typical temperature range
- One sentence on conditions / rainfall
- 3–5 packing essentials only
"""

    # ── Transport section instruction ─────────────────────────────
    transport_instruction = ""
    if origin:
        price_note = (
            f"Use the specific dates {params['date_from']} to {params['date_to']} when quoting transport prices — be as specific as possible."
            if has_dates
            else f"Give typical price ranges for {month}."
        )
        transport_instruction = f"""
## ✈️ Getting There from {origin}
{price_note}
- List all realistic transport options (flight, train, ferry, bus, etc.)
- For each: price range in **local currency** and **GBP (£)**, travel time, and booking tip
- Recommend the best option for this trip
"""

    # ── Per-interest sections ──────────────────────────────────────
    interest_sections = ""
    for interest in interests:
        emoji_map = {
            "surf": "🏄", "surfing": "🏄", "yoga": "🧘", "hike": "🥾", "hiking": "🥾",
            "food": "🍽️", "foodie": "🍽️", "culture": "🏛️", "diving": "🤿", "dive": "🤿",
            "snorkeling": "🐠", "snorkel": "🐠", "cycling": "🚴", "bike": "🚴",
            "nightlife": "🎉", "party": "🎉", "wellness": "💆", "spa": "💆",
            "photography": "📸", "shopping": "🛍️", "climbing": "🧗", "kayak": "🚣",
            "sailing": "⛵", "music": "🎵", "art": "🎨", "history": "📜",
        }
        emoji = emoji_map.get(interest.lower(), "🎯")
        interest_sections += f"""
## {emoji} {interest.title()}
Dedicated section for {interest} in {dest}:
- 3–5 specific spots, studios, operators, or venues with names
- Costs in **local currency** and **GBP (£)**
- Best time / conditions for {interest} in {month}
- Practical tips (booking in advance, gear rental, skill level, etc.)
"""

    # ── Accommodation price note ───────────────────────────────────
    accom_note = (
        f"Where possible quote prices for the specific dates {params['date_from']}–{params['date_to']} (prices may be higher/lower than average)."
        if has_dates
        else f"Give typical nightly rates for {month}."
    )

    return f"""You are an expert travel researcher. Based on the web search results below, write a comprehensive travel brief.

**Destination:** {dest}
**Travel period:** {time_label}
**Duration:** {duration}
**Interests:** {", ".join(interests) if interests else "general sightseeing"}
{"**Travelling from:** " + origin if origin else ""}

Produce a well-structured Markdown travel brief with EXACTLY these sections in this order:

# 🌍 {dest} Travel Brief — {time_label}

## 📋 Overview
Short paragraph on why {dest} is great during {time_label}, vibe, who it suits.

{weather_instruction}

## 🌟 Top Things To Do
Must-do experiences that every visitor should consider, regardless of specific interests:
- At least 6 specific activities/attractions with brief descriptions
- Include entry fees in **local currency** and **GBP (£)** where applicable
- Mix iconic highlights and a couple of lesser-known gems
{interest_sections}
## 🏨 Where to Stay
{accom_note}
- 2–3 neighbourhood/area recommendations with character descriptions
- Budget range per night in **local currency** and **GBP (£)**
- Name 1–2 specific well-regarded hotels/hostels per category
{transport_instruction}
## 💰 Budget Guide
| Category | Budget (per day) | Mid-range (per day) |
|---|---|---|
| Accommodation | local + GBP | local + GBP |
| Food | local + GBP | local + GBP |
| Local transport | local + GBP | local + GBP |
| Activities | local + GBP | local + GBP |
| **Total** | **local + GBP** | **local + GBP** |

## 🗓️ Sample {duration} Itinerary
Day-by-day outline with morning / afternoon / evening suggestions, weaving in the specified interests.

## 💡 Insider Tips
- 4–6 practical tips specific to {dest} and {time_label}

## 🔗 Useful Links
List every source URL used.

---
*Brief generated on {datetime.now().strftime("%d %B %Y")} by Travel Research Agent*

---

RULES:
- ALL prices in both **local currency** and **GBP (£)**
- Be specific with numbers
- Base content on the research data below
- Note estimates where data is missing

## RESEARCH DATA
{_format_forecast(forecast) + chr(10) * 3 if forecast else ""}
{research}
"""


def run_agent(params: dict) -> str:
    forecast = None
    if params.get("date_from") and params.get("date_to"):
        forecast = _get_weather_forecast(params["destination"], params["date_from"], params["date_to"])

    research = _gather_research(params)
    prompt = _build_brief_prompt(params, research, forecast)

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert travel researcher who writes clear, detailed, and practical "
                "travel briefs. Always include costs in both local currency and GBP. Be specific "
                "and helpful. Follow the exact section structure requested."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    return _llm(messages, temperature=0.4)
