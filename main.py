from fastapi import FastAPI
import uvicorn
from src.gmap_service import get_optimized_route
from src.schema import RouteRequest, RouteResponse

app = FastAPI(title="Route Optimizer", description="Optimize routes for given list of address and return the optimized route with the shortest distance and time.")

@app.post("/optimize-route")
def optimize_route(route_request: RouteRequest) -> RouteResponse:
    return get_optimized_route(route_request)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
