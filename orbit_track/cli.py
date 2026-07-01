import sys
import click
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from orbit_track.agents import graph
from orbit_track.mcp_server import run_standalone

async def run_prediction_pipeline(satellite_name: str, location: str):
    """Start the custom MCP server and execute the LangGraph agent pipeline."""
    # Start the MCP server using python -m orbit_track.mcp_server as a subprocess
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "orbit_track.mcp_server"],
        env=None
    )
    
    click.echo("[i] Connecting to OrbitTrack MCP Server...")
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                click.echo("[*] Initializing MCP Session...")
                await session.initialize()
                
                # Pass the active MCP session to the agent nodes via LangGraph config
                config = {"configurable": {"mcp_session": session}}
                
                initial_state = {
                    "satellite_name": satellite_name,
                    "location": location,
                    "logs": [],
                    "tle_data": None,
                    "coordinates": None,
                    "pass_info": None,
                    "discord_message": None,
                    "post_status": None,
                    "error": None
                }
                
                click.echo("[>] Running LangGraph Multi-Agent Pipeline...")
                result = await graph.ainvoke(initial_state, config=config)
                return result
    except Exception as e:
        click.echo(f"[Error] Critical Error running pipeline: {e}", err=True)
        return {
            "error": f"Failed to run pipeline: {str(e)}",
            "logs": [f"Pipeline Execution Failed: {str(e)}"]
        }

@click.command()
@click.option("--predict", "-p", type=str, help="Satellite name to predict (e.g. 'ISS' or 'NOAA 19')")
@click.option("--location", "-l", type=str, default="New York", help="Location to predict from (e.g. city, ZIP code, or address. default: 'New York')")
@click.option("--run-server", is_flag=True, help="Start the custom MCP server standalone on stdio transport")
@click.option("--run-bot", is_flag=True, help="Start the interactive Discord bot")
def main(predict, location, run_server, run_bot):
    """OrbitTrack Developer CLI - Satellite Tracking Multi-Agent System"""
    if run_server:
        # Run MCP server standalone on stdio
        run_standalone()
        return

    if run_bot:
        # Start the interactive Discord Bot
        from orbit_track.bot import start_bot
        start_bot()
        return

    if not predict:
        click.echo("Error: Please provide a satellite name to predict using --predict or run the MCP server with --run-server.")
        click.echo("Use 'orbit-track --help' for usage details.")
        sys.exit(1)
        
    # Execute pipeline synchronously using asyncio.run
    result = asyncio.run(run_prediction_pipeline(predict, location))
    
    # Print execution logs
    click.echo("\n--- Agent Execution Logs ---")
    for log in result.get("logs", []):
        # Filter out unicode characters from logs to prevent console encoding issues
        clean_log = log.encode('ascii', 'replace').decode('ascii')
        click.echo(f"  - {clean_log}")
    click.echo("----------------------------\n")
    
    # Check for errors
    if result.get("error"):
        click.echo(f"[Error] Error: {result['error']}", err=True)
        sys.exit(1)
        
    # Success output
    click.echo("[Success] Pipeline Completed Successfully!")
    click.echo(f"Satellite Match: {result['tle_data']['satellite_name']}")
    if result['pass_info'].get('pass_found'):
        click.echo(f"Discord Alert Status: {result['post_status']}")
    else:
        click.echo(f"Warning: Prediction Status: {result['pass_info'].get('message')}")
        
if __name__ == "__main__":
    main()
