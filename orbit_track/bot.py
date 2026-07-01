import os
import discord
from discord import app_commands
from dotenv import load_dotenv
from orbit_track.cli import run_prediction_pipeline

# Load env variables
load_dotenv()
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

class OrbitBot(discord.Client):
    def __init__(self):
        # Default intents are sufficient for slash commands
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})", flush=True)
        guild_id = os.environ.get("DISCORD_DEV_GUILD_ID")
        if guild_id:
            try:
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                print(f"Slash commands synced instantly to Guild ID: {guild_id}", flush=True)
            except Exception as e:
                print(f"Error syncing to Guild ID {guild_id}: {e}. Falling back to global sync...", flush=True)
                await self.tree.sync()
        else:
            await self.tree.sync()
            print("Slash commands synced globally. Note: Discord may take up to 2 hours to register global updates.", flush=True)

bot = OrbitBot()

@bot.tree.command(name="track", description="Predict the next flyover pass of a satellite")
@app_commands.describe(
    satellite="The name of the satellite (e.g. ISS, Hubble, NOAA 19)",
    location="Your location (e.g. City, Country, ZIP code, or Address)"
)
async def track_satellite(interaction: discord.Interaction, satellite: str, location: str):
    # Acknowledge interaction (gives up to 15 mins for background processing)
    await interaction.response.defer()
    
    # Run the compiled LangGraph tracking pipeline
    result = await run_prediction_pipeline(satellite, location)
    
    # Check for pipeline errors
    if result.get("error"):
        await interaction.followup.send(f"❌ Error executing tracking: {result['error']}")
        return
        
    pass_info = result.get("pass_info", {})
    if not pass_info.get("pass_found"):
        await interaction.followup.send(
            f"⚠️ {pass_info.get('message', 'No pass found for the given parameters.')}"
        )
        return
        
    # Send the formatted message from the Broadcaster Agent
    discord_message = result.get("discord_message")
    await interaction.followup.send(discord_message)

def start_bot():
    """Start the Discord bot."""
    if not BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN is missing in the environment. Please add it to your .env file.", flush=True)
        return
    print("Starting OrbitBot...", flush=True)
    bot.run(BOT_TOKEN)

if __name__ == "__main__":
    start_bot()
