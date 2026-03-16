import requests
import os
import re
import math
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import HTTPException
from dotenv import load_dotenv
from src.constants import GOOGLE_FIELD_MASK
from src.schema import RouteLeg, RouteRequest, RouteResponse
load_dotenv()

GOOGLE_MAPS_API_KEY = (os.getenv("GOOGLE_MAPS_API_KEY") or "").strip()
GOOGLE_MAPS_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
EST = timezone(timedelta(hours=-5))


def _parse_base_departure_time(departure_time: str | None) -> datetime:
    if not departure_time or departure_time.strip().lower() == "now":
        return datetime.now(EST)

    normalized = departure_time.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(EST)

    if parsed.tzinfo is None:
        # Treat naive incoming datetimes as EST per API contract.
        return parsed.replace(tzinfo=EST)

    return parsed


def _duration_to_seconds(raw_duration: Any, localized_duration_text: str | None) -> int:
    if isinstance(raw_duration, str) and raw_duration.endswith("s"):
        numeric_part = raw_duration[:-1]
        try:
            return int(float(numeric_part))
        except ValueError:
            pass

    if localized_duration_text:
        hours_match = re.search(r"(\d+)\s*h", localized_duration_text, re.IGNORECASE)
        mins_match = re.search(r"(\d+)\s*m", localized_duration_text, re.IGNORECASE)
        secs_match = re.search(r"(\d+)\s*s", localized_duration_text, re.IGNORECASE)

        hours = int(hours_match.group(1)) if hours_match else 0
        minutes = int(mins_match.group(1)) if mins_match else 0
        seconds = int(secs_match.group(1)) if secs_match else 0
        total_seconds = (hours * 3600) + (minutes * 60) + seconds
        if total_seconds > 0:
            return total_seconds

    return 0


def _to_utc_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_est_offset(dt: datetime) -> str:
    return dt.astimezone(EST).isoformat(timespec="seconds")


def _normalize_departure_for_google(dt: datetime, explicit_departure: bool) -> datetime:
    now_utc = datetime.now(timezone.utc)
    if dt.astimezone(timezone.utc) > now_utc:
        return dt

    if explicit_departure:
        # Keep the user's local EST clock time (e.g. 10:00) and roll forward by day.
        adjusted = dt
        while adjusted.astimezone(timezone.utc) <= now_utc:
            adjusted += timedelta(days=1)
        return adjusted

    return (now_utc + timedelta(minutes=2)).astimezone(EST)


def _format_distance_km(distance_meters: Any) -> str:
    try:
        meters = float(distance_meters)
    except (TypeError, ValueError):
        return ""

    kilometers = meters / 1000
    kilometers_rounded = round(kilometers, 1)
    if kilometers_rounded.is_integer():
        return f"{int(kilometers_rounded)} km"
    return f"{kilometers_rounded} km"


def _format_duration_text(raw_duration: Any) -> str:
    total_seconds = _duration_to_seconds(raw_duration, None)
    if total_seconds <= 0:
        return ""

    rounded_minutes = math.ceil(total_seconds / 60)
    if rounded_minutes < 60:
        return f"{rounded_minutes}min"

    hours = rounded_minutes // 60
    minutes = rounded_minutes % 60
    if minutes == 0:
        return f"{hours}hr"
    return f"{hours}hr {minutes}min"

def get_optimized_route(route_request: RouteRequest) -> RouteResponse:
    requested_departure_time = _parse_base_departure_time(route_request.departure_time)
    has_explicit_departure = bool(
        route_request.departure_time and route_request.departure_time.strip().lower() != "now"
    )
    google_departure_time = _normalize_departure_for_google(
        requested_departure_time,
        has_explicit_departure
    )
    base_departure_time = requested_departure_time if has_explicit_departure else google_departure_time
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": GOOGLE_FIELD_MASK
    }
    payload = {
        "origin": {"address": route_request.source},
        "destination": {"address": route_request.destination},
        "intermediates": [{"address": waypoint} for waypoint in route_request.waypoints],
        "departureTime": _to_utc_z(google_departure_time),
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "optimizeWaypointOrder": True,
        "computeAlternativeRoutes": False,
        "routeModifiers": {
            "avoidTolls": False,
            "avoidHighways": False,
            "avoidFerries": False
        },
        "languageCode": "en-US",
        "extraComputations": ["FUEL_CONSUMPTION"],
        "units": "METRIC"
    }

    # print("payload: ", payload)
    response = requests.post(GOOGLE_MAPS_URL, headers=headers, json=payload).json()
    if response.get("error"):
        raise HTTPException(status_code=400, detail=response["error"].get("message", "Google Maps API error"))

    routes = response.get("routes", [])
    if not routes:
        raise HTTPException(status_code=404, detail="No route found for the provided addresses.")

    optimized_indices: list[int] = []
    if routes:
        optimized_indices = routes[0].get("optimizedIntermediateWaypointIndex", [])

    optimized_waypoint_addresses = [
        route_request.waypoints[index]
        for index in optimized_indices
        if isinstance(index, int) and 0 <= index < len(route_request.waypoints)
    ]

    # If API doesn't return optimized indices, keep original waypoint order.
    if not optimized_waypoint_addresses and route_request.waypoints:
        optimized_waypoint_addresses = route_request.waypoints

    response["optimizedWaypointAddresses"] = optimized_waypoint_addresses
    response["optimizedAddressList"] = [
        route_request.source,
        *optimized_waypoint_addresses,
        route_request.destination,
    ]
    optimized_address_list = response["optimizedAddressList"]
    first_route_legs = routes[0].get("legs", []) if routes else []
    route_legs: list[dict[str, Any]] = []
    current_departure_time = base_departure_time
    for index in range(len(optimized_address_list) - 1):
        current_leg = first_route_legs[index] if index < len(first_route_legs) else {}
        localized_values = current_leg.get("localizedValues") or current_leg.get("localizedValue") or {}
        localized_duration_text = (localized_values.get("duration") or {}).get("text")
        duration_text = localized_duration_text or str(current_leg.get("duration") or "")
        distance_text = (localized_values.get("distance") or {}).get("text")
        if not distance_text and current_leg.get("distanceMeters") is not None:
            distance_text = f"{current_leg.get('distanceMeters')} m"
        leg_duration_seconds = _duration_to_seconds(current_leg.get("duration"), localized_duration_text)
        current_arrival_time = current_departure_time + timedelta(seconds=leg_duration_seconds)

        route_legs.append(
            RouteLeg(
                from_address=optimized_address_list[index],
                to_address=optimized_address_list[index + 1],
                duration=duration_text,
                distance=distance_text or "",
                departure_time=_to_est_offset(current_departure_time),
                arrival_time=_to_est_offset(current_arrival_time),
            ).model_dump(exclude_none=True)
        )
        current_departure_time = current_arrival_time

    # response["routeLegs"] = route_legs

    first_route = routes[0] if routes else {}
    return RouteResponse(
        status="success",
        total_distance=_format_distance_km(first_route.get("distanceMeters")),
        total_duration_minutes=_format_duration_text(first_route.get("duration")),
        route_legs=route_legs
    )
        