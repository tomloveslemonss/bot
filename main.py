import discord
import os
import json
import asyncio
import time
from discord.ext import tasks
from discord import app_commands
from flask import Flask
from threading import Thread

# Get token from environment variable
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    print("Error: DISCORD_TOKEN environment variable not set!")
    exit(1)

REQUEST_CHANNEL_ID = 1420810819746267217
ADMIN_CHANNEL_ID = 1420810932367655154

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.messages = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# Persistent requests
REQUESTS_FILE = "requests.json"
REQUESTS_BACKUP = "requests.bak"
requests_lock = asyncio.Lock()

def load_requests():
    for file_path in [REQUESTS_FILE, REQUESTS_BACKUP]:
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                for req in data:
                    if 'created_at' not in req:
                        req['created_at'] = time.time()
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return []

def save_requests(requests_data):
    try:
        if os.path.exists(REQUESTS_FILE):
            os.replace(REQUESTS_FILE, REQUESTS_BACKUP)
        temp_file = REQUESTS_FILE + ".tmp"
        with open(temp_file, 'w') as f:
            json.dump(requests_data, f, indent=2)
        os.replace(temp_file, REQUESTS_FILE)
    except Exception as e:
        print(f"Error saving requests: {e}")

requests = load_requests()

# Default role IDs
artist_roles = {
    "carti": 1360191765436432497,
    "ken carson": 1359937447370424440,
    "lucki": 1360191867119075399,
    "other": 1360192004100980826
}

# Keep bot alive
app = Flask('')

@app.route('/')
def home():
    return "Discord Bot is running!"

def run():
    app.run(host='0.0.0.0', port=5000)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

# /request command: free-form artist, mentions requester
@tree.command(name="request", description="Submit a new request")
@app_commands.describe(artist="Artist name", name="Name of the request", link="Spotify, YouTube, SoundCloud link")
async def request(interaction: discord.Interaction, artist: str, name: str, link: str):
    artist_lower = artist.lower()
    role_id = artist_roles.get(artist_lower, artist_roles["other"])

    request_channel = bot.get_channel(REQUEST_CHANNEL_ID)
    if not request_channel:
        await interaction.response.send_message("Error: Could not find requests channel.", ephemeral=True)
        return

    # Send request exactly once, mention user
    msg = await request_channel.send(
        f"**{name}** ({artist})\n{link}\nVote by reacting 👍\nRequested by {interaction.user.mention}"
    )
    await msg.add_reaction("👍")

    new_request = {
        "artist": artist,
        "name": name,
        "link": link,
        "message_id": msg.id,
        "created_at": time.time(),
        "requested_by": str(interaction.user)
    }

    async with requests_lock:
        requests.append(new_request)
        save_requests(requests)

    await interaction.response.send_message(f"Request added: {name} ({artist})", ephemeral=True)

# Ping roles every 24h
@tasks.loop(hours=24)
async def ping_roles():
    channel = bot.get_channel(REQUEST_CHANNEL_ID)
    if not channel:
        print("Error: requests channel not found for ping")
        return
    role_ping = " ".join([f"<@&{rid}>" for rid in artist_roles.values()])
    await channel.send(f"Vote reminder! {role_ping}")

# Calculate votes every 15 minutes for requests older than 48h
@tasks.loop(minutes=15)
async def calculate_votes():
    async with requests_lock:
        current_time = time.time()
        old_requests = [r for r in requests if current_time - r.get('created_at', 0) >= 48*3600]
        if not old_requests:
            return

    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    request_channel = bot.get_channel(REQUEST_CHANNEL_ID)
    if not admin_channel or not request_channel:
        print("Error: admin or request channel not found")
        return

    votes_count = []
    processed = []

    for r in old_requests:
        try:
            msg = await request_channel.fetch_message(r['message_id'])
            for reaction in msg.reactions:
                if str(reaction.emoji) == "👍":
                    votes_count.append({
                        "artist": r['artist'],
                        "name": r['name'],
                        "link": r['link'],
                        "votes": reaction.count - 1
                    })
                    processed.append(r)
                    break
        except Exception as e:
            print(f"Error fetching message {r['message_id']}: {e}")

    if processed:
        async with requests_lock:
            for p in processed:
                if p in requests:
                    requests.remove(p)
            save_requests(requests)

        if votes_count:
            top = sorted(votes_count, key=lambda x: x['votes'], reverse=True)[:5]
            msg_text = "**Top 5 Requests (48h period):**\n"
            for r in top:
                msg_text += f"{r['name']} ({r['artist']}): {r['link']} - {r['votes']} votes\n"
            await admin_channel.send(msg_text)
        else:
            await admin_channel.send("No votes found for processed requests.")

    print(f"Processed {len(processed)} requests out of {len(old_requests)} eligible")

@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    keep_alive()
    ping_roles.start()
    calculate_votes.start()
    await calculate_votes()  # process overdue immediately
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.event
async def on_error(event, *args, **kwargs):
    print(f"Error in {event}: {args}, {kwargs}")

if __name__ == "__main__":
    bot.run(TOKEN)
