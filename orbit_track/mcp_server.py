import datetime
import requests
import sys
from mcp.server.fastmcp import FastMCP
from skyfield.api import load, wgs84, EarthSatellite

# Initialize FastMCP Server
mcp = FastMCP("orbit-track-server")

@mcp.tool()
def fetch_tle(satellite_name: str) -> dict:
    """
    Fetch TLE (Two-Line Element) orbital data for a satellite from CelesTrak.
    
    Args:
        satellite_name: The name of the satellite (e.g. "ISS" or "NOAA 19").
        
    Returns:
        A dictionary containing satellite_name, tle_line1, and tle_line2.
    """
    # Clean up name for CelesTrak URL formatting
    query_name = satellite_name.strip()
    url = f"https://celestrak.org/NORAD/elements/gp.php?NAME={query_name}&FORMAT=TLE"
    
    try:
        response = requests.get(url, timeout=10)
    except Exception as e:
        raise RuntimeError(f"Error connecting to CelesTrak: {str(e)}")
        
    if response.status_code != 200:
        raise ValueError(f"Failed to fetch TLE from CelesTrak. HTTP Status: {response.status_code}")
    
    content = response.text.strip()
    lines = [line.strip() for line in content.split('\n') if line.strip()]
    
    # CelesTrak returns "No GP data found" or empty string when no match is found
    if not lines or "No GP data found" in content or len(lines) < 3:
        raise ValueError(f"Satellite '{satellite_name}' not found on CelesTrak. Please check the spelling.")
    
    # Standard TLE format from gp.php?FORMAT=TLE returns:
    # Line 0: Satellite Name (e.g. ISS (ZARYA))
    # Line 1: TLE Line 1
    # Line 2: TLE Line 2
    return {
        "satellite_name": lines[0],
        "tle_line1": lines[1],
        "tle_line2": lines[2]
    }

@mcp.tool()
def geocode_location(location: str) -> dict:
    """
    Geocode a location string (city, country, address, or ZIP code) to latitude and longitude coordinates using Nominatim.
    
    Args:
        location: A location query string (e.g. "Paris, France", "London", "94103").
        
    Returns:
        A dictionary containing location, latitude, longitude, and display_name.
    """
    clean_location = location.strip()
    if not clean_location:
        raise ValueError("Location string cannot be empty.")
        
    # Nominatim requires a user agent identifying the application
    headers = {"User-Agent": "OrbitTrackAgent/1.0 (contact: developer@orbittrack.local)"}
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": clean_location,
        "format": "jsonv2"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
    except Exception as e:
        raise RuntimeError(f"Error connecting to Nominatim geocoding service: {str(e)}")
        
    if response.status_code != 200:
        raise ValueError(f"Nominatim service returned HTTP status {response.status_code}")
        
    data = response.json()
    if not data:
        raise ValueError(f"Location '{clean_location}' not found.")
        
    res_location = data[0]
    return {
        "location": clean_location,
        "latitude": float(res_location["lat"]),
        "longitude": float(res_location["lon"]),
        "display_name": res_location.get("display_name", "")
    }

@mcp.tool()
def calculate_flyover(
    tle_line1: str, 
    tle_line2: str, 
    satellite_name: str, 
    latitude: float, 
    longitude: float, 
    elevation_m: float = 0.0
) -> dict:
    """
    Calculate the next flyover events for a satellite over a given latitude and longitude.
    
    Args:
        tle_line1: TLE Line 1.
        tle_line2: TLE Line 2.
        satellite_name: Name of the satellite.
        latitude: Latitude of observer in degrees.
        longitude: Longitude of observer in degrees.
        elevation_m: Elevation of observer in meters (optional, default 0.0).
        
    Returns:
        A dictionary containing the next pass info (rise, peak, set times, max elevation degrees, duration seconds).
    """
    try:
        ts = load.timescale()
        satellite = EarthSatellite(tle_line1, tle_line2, satellite_name, ts)
        location = wgs84.latlon(latitude, longitude, elevation_m=elevation_m)
        
        # Search from current time up to 48 hours in the future
        t0 = ts.now()
        t1 = ts.utc(t0.utc_datetime() + datetime.timedelta(days=2))
        
        # Find events (altitude_degrees=10.0 sets the minimum elevation for the pass)
        t, events = satellite.find_events(location, t0, t1, altitude_degrees=10.0)
        
        if len(t) == 0:
            return {
                "pass_found": False,
                "message": "No passes found in the next 48 hours above 10 degrees elevation."
            }
            
        passes = []
        current_pass = {}
        
        for ti, event in zip(t, events):
            event_time_utc = ti.utc_datetime().replace(tzinfo=datetime.timezone.utc)
            event_time_str = event_time_utc.isoformat()
            
            # event type: 0 = rise, 1 = culminate, 2 = set
            if event == 0:
                current_pass = {"rise_time": event_time_str}
            elif event == 1 and "rise_time" in current_pass:
                difference = satellite - location
                topocentric = difference.at(ti)
                alt, az, distance = topocentric.altaz()
                current_pass["culminate_time"] = event_time_str
                current_pass["max_elevation_deg"] = alt.degrees
                current_pass["culminate_azimuth_deg"] = az.degrees
            elif event == 2 and "rise_time" in current_pass:
                current_pass["set_time"] = event_time_str
                # Calculate duration
                rise_dt = datetime.datetime.fromisoformat(current_pass["rise_time"])
                set_dt = datetime.datetime.fromisoformat(current_pass["set_time"])
                duration_sec = (set_dt - rise_dt).total_seconds()
                current_pass["duration_sec"] = duration_sec
                passes.append(current_pass)
                current_pass = {}
                # Stop after the first complete upcoming pass
                break
                
        if not passes:
            return {
                "pass_found": False,
                "message": "No complete pass (rise, culmination, and set) found in the next 48 hours."
            }
            
        next_pass = passes[0]
        next_pass["pass_found"] = True
        return next_pass
        
    except Exception as e:
        raise RuntimeError(f"Error calculating orbital pass: {str(e)}")

def run_standalone():
    """Run the MCP server in stdio transport mode."""
    # Ensure logs output to stderr, not stdout, to avoid corrupting json-rpc stream
    print("Starting OrbitTrack MCP Server on stdio transport...", file=sys.stderr)
    mcp.run(transport="stdio")

if __name__ == "__main__":
    run_standalone()
