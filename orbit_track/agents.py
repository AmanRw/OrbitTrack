import os
import json
import asyncio
import datetime
from typing import TypedDict, List, Dict, Any, Optional
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END

from orbit_track.utils import get_env_var, format_utc_to_local, format_duration, post_to_discord

# ----------------------------------------------------
# 1. State Definition
# ----------------------------------------------------
class AgentState(TypedDict):
    satellite_name: str
    location: str
    tle_data: Optional[Dict[str, Any]]
    coordinates: Optional[Dict[str, Any]]
    pass_info: Optional[Dict[str, Any]]
    discord_message: Optional[str]
    post_status: Optional[str]
    logs: List[str]
    error: Optional[str]

# ----------------------------------------------------
# 2. LLM Initialization Helper
# ----------------------------------------------------
def get_llm():
    api_key = get_env_var("GEMINI_API_KEY")
    if not api_key:
        return None
    # Using gemini-1.5-flash for compatibility and speed
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=api_key,
        temperature=0.3
    )

# Helper to call MCP tools securely
async def call_mcp_tool(config: RunnableConfig, tool_name: str, arguments: dict) -> dict:
    session = config.get("configurable", {}).get("mcp_session")
    if not session:
        raise RuntimeError("MCP session not found in agent configuration.")
    
    result = await session.call_tool(tool_name, arguments)
    if not result.content:
        raise ValueError(f"MCP tool '{tool_name}' returned no content.")
        
    text = result.content[0].text
    
    # If the MCP server returned an error, raise it so the agent can catch and report it properly
    if getattr(result, "isError", False):
        raise ValueError(text)
        
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # If not JSON, return wrapped in a dict
        return {"result": text}

# ----------------------------------------------------
# 3. Agent 1: Tracker Node
# ----------------------------------------------------
async def tracker_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    logs = list(state.get("logs", []))
    logs.append("Tracker Agent: Initiated TLE fetch.")
    
    satellite_name = state["satellite_name"]
    llm = get_llm()
    
    # AI-assisted name normalization if API key is present
    if llm:
        try:
            prompt = (
                f"You are the Tracker Agent. The user wants to track a satellite using '{satellite_name}'. "
                "CelesTrak expects official satellite names (e.g. 'ISS (ZARYA)' for ISS, 'NOAA 19' for noaa19, 'TIANHE' for tianhe). "
                "Suggest the most likely official satellite name for a CelesTrak query. "
                "Respond with ONLY the name, and nothing else. Do not add any punctuation or explanation."
            )
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            normalized_name = response.content.strip()
            logs.append(f"Tracker Agent: Normalized satellite name '{satellite_name}' -> '{normalized_name}' using Gemini.")
            satellite_name = normalized_name
        except Exception as e:
            logs.append(f"Tracker Agent: Gemini name normalization failed ({e}). Falling back to original input.")
    
    try:
        # Fetch TLE using MCP server tool
        tle_res = await call_mcp_tool(config, "fetch_tle", {"satellite_name": satellite_name})
        logs.append(f"Tracker Agent: Successfully fetched TLE for '{tle_res['satellite_name']}'.")
        return {
            "tle_data": tle_res,
            "logs": logs
        }
    except Exception as e:
        error_msg = f"Tracker Agent Error: {str(e)}"
        logs.append(error_msg)
        return {
            "error": error_msg,
            "logs": logs
        }

# ----------------------------------------------------
# 4. Agent 2: Astronomer Node
# ----------------------------------------------------
async def astronomer_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    logs = list(state.get("logs", []))
    if state.get("error"):
        # Skip if previous agent failed
        return {}
        
    logs.append("Astronomer Agent: Geocoding location and calculating pass.")
    
    tle_data = state["tle_data"]
    location = state["location"]
    
    try:
        # Step 1: Geocode Location
        geo_res = await call_mcp_tool(config, "geocode_location", {"location": location})
        lat = geo_res["latitude"]
        lon = geo_res["longitude"]
        display_name = geo_res["display_name"]
        logs.append(f"Astronomer Agent: Geocoded location '{location}' to Lat: {lat}, Lon: {lon} ({display_name}).")
        
        # Step 2: Calculate Flyover
        pass_res = await call_mcp_tool(config, "calculate_flyover", {
            "tle_line1": tle_data["tle_line1"],
            "tle_line2": tle_data["tle_line2"],
            "satellite_name": tle_data["satellite_name"],
            "latitude": lat,
            "longitude": lon
        })
        
        if not pass_res.get("pass_found", False):
            msg = pass_res.get("message", "No pass found.")
            logs.append(f"Astronomer Agent: {msg}")
            return {
                "coordinates": geo_res,
                "pass_info": pass_res,
                "logs": logs
            }
            
        logs.append("Astronomer Agent: Successfully calculated next upcoming pass.")
        return {
            "coordinates": geo_res,
            "pass_info": pass_res,
            "logs": logs
        }
        
    except Exception as e:
        error_msg = f"Astronomer Agent Error: {str(e)}"
        logs.append(error_msg)
        return {
            "error": error_msg,
            "logs": logs
        }

# ----------------------------------------------------
# 5. Agent 3: Broadcaster Node
# ----------------------------------------------------
async def broadcaster_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    logs = list(state.get("logs", []))
    if state.get("error"):
        # Skip if previous agent failed
        return {}
        
    logs.append("Broadcaster Agent: Preparing Discord payload.")
    
    tle_data = state["tle_data"]
    coordinates = state["coordinates"]
    pass_info = state["pass_info"]
    
    # Check if a pass was actually found
    if not pass_info.get("pass_found", False):
        msg = f"No upcoming passes found for {tle_data['satellite_name']} at location '{state['location']}' in the next 48 hours."
        logs.append("Broadcaster Agent: No pass found to broadcast.")
        return {
            "discord_message": msg,
            "post_status": "SKIPPED (No Pass)",
            "logs": logs
        }
        
    # Local Time Conversions
    local_rise = format_utc_to_local(pass_info["rise_time"])
    local_culminate = format_utc_to_local(pass_info["culminate_time"])
    local_set = format_utc_to_local(pass_info["set_time"])
    duration_str = format_duration(pass_info["duration_sec"])
    max_elev = round(pass_info["max_elevation_deg"], 1)
    peak_az = round(pass_info["culminate_azimuth_deg"], 1)
    
    llm = get_llm()
    discord_message = ""
    
    # AI-assisted writing if API key is present
    if llm:
        try:
            prompt = (
                "You are the Broadcaster Agent. Write a highly engaging Discord alert message for an upcoming satellite flyover. "
                "Use emojis, bold text, and formatting where appropriate. Make it look professional.\n\n"
                f"Satellite: {tle_data['satellite_name']}\n"
                f"Location: {state['location']} ({coordinates['display_name']}) at Lat: {coordinates['latitude']}, Lon: {coordinates['longitude']}\n"
                f"Pass Rise Time (Local): {local_rise}\n"
                f"Pass Peak Time (Local): {local_culminate}\n"
                f"Pass Set Time (Local): {local_set}\n"
                f"Duration: {duration_str}\n"
                f"Max Elevation: {max_elev}° above horizon\n"
                f"Peak Azimuth Direction: {peak_az}°\n\n"
                "Respond with ONLY the final Discord post content. Do not include markdown code block fences."
            )
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            discord_message = response.content.strip()
            logs.append("Broadcaster Agent: Generated Discord alert text using Gemini.")
        except Exception as e:
            logs.append(f"Broadcaster Agent: Gemini message generation failed ({e}). Falling back to template.")
            llm = None  # trigger fallback
            
    # Default fallback template if no LLM
    if not llm:
        discord_message = (
            f"🛰️ **Satellite Flyover Alert: {tle_data['satellite_name']}** 🛰️\n\n"
            f"📍 **Location:** {state['location']} ({coordinates['display_name'].split(',')[0]})\n"
            f"Coordinates: `{coordinates['latitude']}, {coordinates['longitude']}`\n\n"
            f"⏰ **Flyover Schedule (Local Time):**\n"
            f"• **Rises:** {local_rise}\n"
            f"• **Peak (Culmination):** {local_culminate}\n"
            f"• **Sets:** {local_set}\n\n"
            f"✨ **Pass Details:**\n"
            f"• **Duration:** {duration_str}\n"
            f"• **Max Elevation:** {max_elev}° above the horizon\n"
            f"• **Peak Direction (Azimuth):** {peak_az}°\n\n"
            f"_Powered by OrbitTrack Multi-Agent System (LangGraph & MCP)_"
        )
        logs.append("Broadcaster Agent: Prepared template-based Discord alert text.")

    # Format the message as a rich Discord Embed
    embed = {
        "title": f"🛰️ Satellite Flyover Alert: {tle_data['satellite_name']}",
        "color": 3447003, # Premium Blue
        "description": f"An upcoming pass of the satellite **{tle_data['satellite_name']}** has been detected over your location.",
        "fields": [
            {
                "name": "📍 Location",
                "value": f"{state['location']} ({coordinates['display_name'].split(',')[0]})\nLat: `{coordinates['latitude']}`, Lon: `{coordinates['longitude']}`",
                "inline": False
            },
            {
                "name": "⏰ Local Schedule",
                "value": f"**Rises:** {local_rise}\n**Peak:** {local_culminate}\n**Sets:** {local_set}",
                "inline": True
            },
            {
                "name": "✨ Pass Details",
                "value": f"**Duration:** {duration_str}\n**Max Elev:** {max_elev}°\n**Direction:** {peak_az}°",
                "inline": True
            }
        ],
        "footer": {
            "text": "OrbitTrack Multi-Agent Pipeline • Powered by LangGraph & MCP"
        },
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    
    # Broadcast message to Discord
    webhook_url = get_env_var("DISCORD_WEBHOOK_URL")
    if webhook_url:
        success = post_to_discord(webhook_url, content=discord_message, embeds=[embed])
        if success:
            post_status = "SUCCESS"
            logs.append("Broadcaster Agent: Successfully posted alert to Discord channel.")
        else:
            post_status = "FAILED"
            logs.append("Broadcaster Agent: Failed to post alert to Discord webhook.")
    else:
        post_status = "MOCKED (No Webhook URL configured)"
        logs.append("Broadcaster Agent: Mock-posted (DISCORD_WEBHOOK_URL is missing).")
        # Print to stdout/stderr in developers log so they can inspect safely on Windows
        safe_message = discord_message.encode('ascii', 'replace').decode('ascii')
        print("\n=== MOCK DISCORD BROADCAST ===", flush=True)
        print(safe_message, flush=True)
        print("==============================\n", flush=True)
        
    return {
        "discord_message": discord_message,
        "post_status": post_status,
        "logs": logs
    }

# ----------------------------------------------------
# 6. Graph Compilation
# ----------------------------------------------------
workflow = StateGraph(AgentState)

# Add agent nodes
workflow.add_node("tracker", tracker_node)
workflow.add_node("astronomer", astronomer_node)
workflow.add_node("broadcaster", broadcaster_node)

# Link nodes sequentially
workflow.add_edge(START, "tracker")
workflow.add_edge("tracker", "astronomer")
workflow.add_edge("astronomer", "broadcaster")
workflow.add_edge("broadcaster", END)

# Compile the pipeline
graph = workflow.compile()
