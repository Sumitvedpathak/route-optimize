from pydantic import BaseModel, Field
from typing import List, Optional

class RouteRequest(BaseModel):
    source: str
    destination: str
    waypoints: List[str]
    departure_time: Optional[str]

class RouteLeg(BaseModel):
    from_address: str = Field(..., example="34 Finney Terrace, Milton, ON")
    to_address: str = Field(..., example="123 Main St, Toronto, ON")
    duration: str = Field(..., example="44 mins")
    distance: str = Field(..., example="60.1 km")
    arrival_time: Optional[str] = Field(default=None, example="2026-03-14T10:00:00Z")
    departure_time: Optional[str] = Field(default=None, example="2026-03-14T09:00:00Z")

class RouteResponse(BaseModel):
    status: str
    total_distance: str
    total_duration_minutes: str
    route_legs: List[RouteLeg]
