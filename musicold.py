import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import yt_dlp
import json
import os
from typing import Optional
from .logger import log_bot_event, log_error
import re
import aiohttp
from .base_cog import BaseCog

CACHE_DIR = "logs/music_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -analyzeduration 0 -probesize 32768',
    'options': '-vn -bufsize 2048k -maxrate 256k'
}

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "extractaudio": True,
    "audioformat": "opus",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": False,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": True,
    "quiet": False,
    "no_warnings": False,
    "verbose": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
    "force-ipv4": True,
    "http_chunk_size": 1048576,
    "extract_flat": False,
    "force_generic_extractor": False, 
    "socket_timeout": 10
    "retries": 5,
    "postprocessors": [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "opus",
        "preferredquality": "192"
    }]
}
URL_REGEX = re.compile(
    r"https?://(?:www\.|m\.)?"
    r"(?:"
    r"(?:youtube\.com/(?:watch\?v=|playlist\?list=|shorts/)|"
    r"youtu\.be/|"
    r"(?:open\.|play\.)?spotify\.com/|"
    r"(?:music\.)?apple\.com/|"
    r"soundcloud\.com/|"
    r"deezer\.com/|"
    r"tidal\.com/|"
    r"(?:www\.)?dailymotion\.com/|"
    r"vimeo\.com/|"
    r"twitch\.tv/|"
    r".*\.(?:mp3|wav|ogg|m4a|webm|mp4)$|"
    r".*)"
    r")"
    r"[\w\-/]+(?:\?[\w=&.-]*)?/?$"
)

class MusicCog(BaseCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.bot = bot
        self.queues = {}
        self.title_cache = {}
        self.audio_cache = {}
        self.ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
        self.loop = asyncio.get_event_loop()

    def get_queue(self, guild_id):
        """Get the queue for a specific guild"""
        if guild_id not in self.queues:
            self.queues[guild_id] = []
        return self.queues[guild_id]

    async def _extract_song_info(self, url: str) -> tuple[str, str, str]:
        """Extract song information from various sources"""
        try:
            print(f"Extracting info from URL: {url}")
            async with aiohttp.ClientSession() as session:
                headers = {'User-Agent': 'Mozilla/5.0'}
                async with session.get(url, headers=headers) as response:
                    print(f"Got response status: {response.status}")
                    if response.status == 200:
                        text = await response.text()
                        print("Successfully got page content")
                        
                        if 'spotify.com' in url.lower():
                            title_match = re.search(r'<meta property="og:title" content="([^"]+)"', text)
                            artist_match = re.search(r'<meta property="music:musician" content="[^"]+">.*?<meta property="music:musician_name" content="([^"]+)"', text, re.DOTALL)
                            if title_match:
                                title = title_match.group(1)
                                artist = artist_match.group(1) if artist_match else ""
                                cleaned_title = re.sub(r' - song by .*$', '', title)
                                return artist, cleaned_title, f"{artist} - {cleaned_title} audio"
                                
                        elif 'music.apple.com' in url.lower():
                            title_match = re.search(r'<meta property="og:title" content="([^"]+)"', text)
                            artist_match = re.search(r'<meta property="og:description" content="([^"]+)".*?', text)
                            if title_match:
                                title = title_match.group(1)
                                artist = ""
                                if artist_match:
                                    artist_desc = artist_match.group(1)
                                    artist = re.search(r'Song \· (.+)', artist_desc)
                                    if artist:
                                        artist = artist.group(1)
                                return artist, title, f"{artist} {title} audio"

                        elif 'music.youtube.com' in url.lower():
                            video_id_match = re.search(r'watch\?v=([a-zA-Z0-9_-]+)', url)
                            if video_id_match:
                                return "", "", f"https://www.youtube.com/watch?v={video_id_match.group(1)}"
                            
                            title_match = re.search(r'<meta property="og:title" content="([^"]+)"', text)
                            if title_match:
                                title = title_match.group(1)
                                return "", title, f"{title} audio"
        except Exception as e:
            print(f"Error extracting song info: {e}")
        
        return "", "", ""

    async def _try_alternative_source(self, query: str) -> tuple[bool, str, str]:
        """Try to find alternative source for DRM protected content"""
        try:
            artist, title, search_query = await self._extract_song_info(query)
            
            if not search_query:
                parts = query.split('/')[-1].replace('-', ' ').split('?')[0]
                search_query = parts

            try:
                print(f"Searching for alternative: {search_query}")
                
                temp_opts = YTDL_OPTIONS.copy()
                temp_opts['noplaylist'] = True
                temp_ytdl = yt_dlp.YoutubeDL(temp_opts)
                
                search_result = await self.loop.run_in_executor(
                    None,
                    lambda: temp_ytdl.extract_info(f"ytsearch1:{search_query}", download=False)
                )
                
                if search_result and 'entries' in search_result and search_result['entries']:
                    video = search_result['entries'][0]
                    if video.get('webpage_url'):
                        print(f"Found alternative: {video.get('title', 'Unknown')}")
                        return True, video['webpage_url'], f"Found alternative: {video.get('title', 'Unknown')}"
                    
                print("No valid entries found in search results")
            except Exception as e:
                print(f"Error searching YouTube: {e}")

            return False, "", "Could not find an alternative source"

        except Exception as e:
            print(f"Error in alternative source search: {e}")
            return False, "", "Error searching for alternative"

    async def _preload_song(self, url):
        """Preload song information and create audio source"""
        try:
            if url in self.audio_cache:
                return self.audio_cache[url]

            info = await self.loop.run_in_executor(
                None,
                lambda: self.ytdl.extract_info(url, download=False)
            )
            
            if not info:
                return None
                
            self.title_cache[url] = {
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0)
            }
            
            audio_url = info.get('url')
            if not audio_url and 'formats' in info:
                formats = [f for f in info['formats'] if f.get('acodec') != 'none']
                if formats:
                    formats.sort(key=lambda x: int(x.get('abr', 0) or 0), reverse=True)
                    audio_url = formats[0]['url']

            if not audio_url:
                return None

            source = await discord.FFmpegOpusAudio.from_probe(audio_url, **FFMPEG_OPTIONS)
            self.audio_cache[url] = {
                'source': source,
                'info': info,
                'created_at': self.loop.time()
            }
            return self.audio_cache[url]

        except Exception as e:
            log_error(e, f"Error preloading {url}")
            return None

    async def play_next(self, guild, text_channel=None):
        """Play the next song in the queue"""
        if not guild.voice_client or not guild.voice_client.is_connected():
            return

        queue = self.get_queue(guild.id)
        if not queue:
            if text_channel:
                await text_channel.send("Queue finished!")
            return

        try:
            url = queue[0]
            cached_data = await self._preload_song(url)

            if not cached_data:
                queue.pop(0)
                await text_channel.send("Could not load the audio.")
                await self.play_next(guild, text_channel)
                return

            queue.pop(0)
            guild.voice_client.play(
                cached_data['source'],
                after=lambda e: asyncio.run_coroutine_threadsafe(
                    self.play_next(guild, text_channel), self.loop
                )
            )
            
            if text_channel:
                title = cached_data['info'].get('title', 'Unknown')
                await text_channel.send(f"Now playing: {title}")

        except Exception as e:
            if text_channel:
                await text_channel.send(f"Error playing song: {str(e)}")
            await self.play_next(guild, text_channel)

    @app_commands.command(name="join", description="Join a voice channel")
    async def join(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("You need to be in a voice channel first!")
            return

        await interaction.response.defer()
        
        try:
            channel = interaction.user.voice.channel
            if interaction.guild.voice_client:
                await interaction.guild.voice_client.move_to(channel)
            else:
                await channel.connect()
            await interaction.followup.send(f"Joined {channel.name}")
        except Exception as e:
            await interaction.followup.send(f"Could not join the channel: {str(e)}")

    @app_commands.command(name="play", description="Play music from URL or search terms")
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server!")
            return

        if not interaction.user.voice:
            await interaction.response.send_message("You need to be in a voice channel!")
            return

        await interaction.response.defer()

        try:
            if not interaction.guild.voice_client:
                await interaction.user.voice.channel.connect()

            is_url = query.startswith(('http://', 'https://'))
            is_playlist = 'playlist' in query or 'list=' in query
            is_spotify = 'spotify.com' in query.lower()
            is_apple_music = 'music.apple.com' in query.lower()

            if is_spotify or is_apple_music:
                await interaction.followup.send("Processing link... Searching for alternative source...")
                success, alt_url, message = await self._try_alternative_source(query)
                if success:
                    query = alt_url
                    await interaction.followup.send(f" {message}")
                else:
                    artist, title, search_query = await self._extract_song_info(query)
                    if search_query:
                        query = f"ytsearch1:{search_query}"
                        await interaction.followup.send(f"Searching for: {title}")
                    else:
                        await interaction.followup.send("Could not extract song information.")
                        return
            elif not is_url:
                await interaction.followup.send(f"Searching for: {query}")
                query = f"ytsearch:{query}"
            elif 'youtu.be' in query:
                video_id = query.split('/')[-1].split('?')[0]
                query = f"https://www.youtube.com/watch?v={video_id}"

            if is_playlist:
                await interaction.followup.send("Processing playlist... This might take a moment.")

            try:
                info = await self.loop.run_in_executor(None, lambda: self.ytdl.extract_info(query, download=False))
            except Exception as e:
                error_msg = str(e).lower()
                if "[drm]" in error_msg or "drm protection" in error_msg:
                    await interaction.followup.send("🔄 DRM protection detected. Searching for alternative source...")
                    
                    success, alt_url, message = await self._try_alternative_source(query)
                    if success:
                        try:
                            info = await self.loop.run_in_executor(
                                None,
                                lambda: self.ytdl.extract_info(alt_url, download=False)
                            )
                            if info:
                                await interaction.followup.send(f"{message}")
                                queue = self.get_queue(interaction.guild.id)
                                url = info.get('webpage_url') or info.get('url')
                                if url:
                                    queue.append(url)
                                    self.title_cache[url] = {
                                        'title': info.get('title', 'Unknown'),
                                        'duration': info.get('duration', 0)
                                    }
                                    if not interaction.guild.voice_client.is_playing():
                                        await self.play_next(interaction.guild, interaction.channel)
                                    return
                        except Exception as e:
                            print(f"Error playing alternative source: {e}")
                    
                    await interaction.followup.send(
                        "Could not find a playable alternative.\n\n"
                        "Try these options instead:\n"
                        "• Use a regular YouTube link\n"
                        "• Search by song name and artist\n"
                        "• Try SoundCloud or other non-DRM sources"
                    )
                elif "video unavailable" in error_msg:
                    await interaction.followup.send("This video is unavailable or private.")
                elif "sign in to confirm your age" in error_msg:
                    await interaction.followup.send("This content is age-restricted.")
                elif "not a supported url" in error_msg:
                    await interaction.followup.send("This URL is not supported. Try using a direct link to the content.")
                elif "geo restriction" in error_msg:
                    await interaction.followup.send("This content is not available in the current region.")
                elif "network error" in error_msg or "connection error" in error_msg:
                    await interaction.followup.send("Network error occurred. Please check your connection and try again.")
                else:
                    log_error(e, f"Error in play command with query: {query}")
                    await interaction.followup.send(f"Error loading content: {str(e)}")
                return

            if not info:
                await interaction.followup.send("Could not find any playable audio!")
                return

            queue = self.get_queue(interaction.guild.id)
            added_tracks = []

            if 'entries' in info:
                valid_entries = [entry for entry in info['entries'] if entry and (entry.get('webpage_url') or entry.get('url'))]
                
                for entry in valid_entries:
                    url = entry.get('webpage_url') or entry.get('url')
                    title = entry.get('title', 'Unknown')
                    duration = entry.get('duration', 0)
                    
                    queue.append(url)
                    self.title_cache[url] = {
                        'title': title,
                        'duration': duration,
                        'webpage_url': url
                    }
                    added_tracks.append(title)

                if added_tracks:
                    embed = discord.Embed(
                        title="Playlist Added to Queue",
                        color=discord.Color.green()
                    )
                    embed.add_field(
                        name="Tracks Added",
                        value=f"{len(added_tracks)} songs",
                        inline=False
                    )
                    if len(added_tracks) > 5:
                        track_list = "\n".join(f"• {title}" for title in added_tracks[:5])
                        embed.add_field(
                            name="First 5 Tracks",
                            value=f"{track_list}\n*...and {len(added_tracks)-5} more*",
                            inline=False
                        )
                    else:
                        track_list = "\n".join(f"• {title}" for title in added_tracks)
                        embed.add_field(
                            name="Track List",
                            value=track_list,
                            inline=False
                        )
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.followup.send("No playable tracks found in playlist")

            else:  # Single track
                url = info.get('webpage_url') or info.get('url')
                title = info.get('title', 'Unknown')
                duration = info.get('duration', 0)
                
                if not url:
                    await interaction.followup.send("Could not process the track!")
                    return
                
                queue.append(url)
                self.title_cache[url] = {
                    'title': title,
                    'duration': duration,
                    'webpage_url': url
                }
                
                embed = discord.Embed(
                    title="🎵 Track Added to Queue",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="Title",
                    value=title,
                    inline=False
                )
                if duration:
                    minutes = duration // 60
                    seconds = duration % 60
                    embed.add_field(
                        name="Duration",
                        value=f"{minutes}:{seconds:02d}",
                        inline=True
                    )
                await interaction.followup.send(embed=embed)

            if not interaction.guild.voice_client.is_playing():
                await self.play_next(interaction.guild, interaction.channel)

        except Exception as e:
            log_error(e, f"Error in play command: {str(e)}")
            await interaction.followup.send(f"An error occurred: {str(e)}")

    @app_commands.command(name="skip", description="Skip current song")
    async def skip(self, interaction: discord.Interaction):
        if not interaction.guild.voice_client:
            await interaction.response.send_message("Not playing any music!")
            return

        if interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
            await interaction.response.send_message("Skipped the current song")
        else:
            await interaction.response.send_message("No song is currently playing")

    @app_commands.command(name="stop", description="Stop playing and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        if not interaction.guild.voice_client:
            await interaction.response.send_message("Not playing any music!")
            return

        queue = self.get_queue(interaction.guild.id)
        queue.clear()
        
        if interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
        
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Stopped playing and cleared the queue!")

    @app_commands.command(name="queue", description="Show the current music queue")
    async def queue(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server!")
            return
            
        await interaction.response.defer()
        
        try:
            embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.blue())
            
            try:
                queue = self.get_queue(interaction.guild.id)
                print(f"Queue for guild {interaction.guild.id}: {queue}")
            except Exception as e:
                print(f"Error getting queue: {e}")
                queue = []
            
            voice_client = getattr(interaction.guild, 'voice_client', None)
            print(f"Voice client: {voice_client}")
            
            try:
                if voice_client and getattr(voice_client, 'is_playing', lambda: False)():
                    current_url = None
                    current_source = getattr(voice_client, 'source', None)
                    
                    for url, cache_data in self.audio_cache.items():
                        if cache_data.get('source') == current_source:
                            current_url = url
                            break
                    
                    print(f"Current URL: {current_url}")
                    print(f"Audio cache: {self.audio_cache}")
                    print(f"Title cache: {self.title_cache}")
                    
                    if current_url and current_url in self.title_cache:
                        info = self.title_cache[current_url]
                        title = info.get('title', 'Unknown')
                        duration = info.get('duration', 0)
                        duration_str = f" ({duration//60}:{duration%60:02d})" if duration else ""
                        embed.add_field(
                            name="Now Playing",
                            value=f"{title}{duration_str}",
                            inline=False
                        )
                    else:
                        try:
                            current = next(iter(self.audio_cache.values()))
                            if current and 'info' in current:
                                title = current['info'].get('title', 'Unknown')
                                duration = current['info'].get('duration', 0)
                                duration_str = f" ({duration//60}:{duration%60:02d})" if duration else ""
                                embed.add_field(
                                    name="Now Playing",
                                    value=f"🎵 {title}{duration_str}",
                                    inline=False
                                )
                            else:
                                raise KeyError("No valid info in audio cache")
                        except (StopIteration, KeyError) as e:
                            print(f"Fallback error: {e}")
                            embed.add_field(
                                name="Now Playing",
                                value="🎵 Unknown Track",
                                inline=False
                            )
                else:
                    print("No song is currently playing")
                    embed.add_field(
                        name="Now Playing",
                        value="*Nothing is playing*",
                        inline=False
                    )
            except Exception as e:
                print(f"Error getting current song: {e}")
                embed.add_field(
                    name="Now Playing",
                    value="*Error getting current song*",
                    inline=False
                )

            try:
                if queue:
                    queue_text = []
                    for i, url in enumerate(queue[:10], 1):
                        try:
                            info = self.title_cache.get(url, {})
                            title = info.get('title', 'Loading...')
                            duration = info.get('duration', 0)
                            duration_str = f" ({duration//60}:{duration%60:02d})" if duration else ""
                            queue_text.append(f"`{i}.` {title}{duration_str}")
                        except Exception as e:
                            print(f"Error processing queue item {i}: {e}")
                            queue_text.append(f"`{i}.` Error getting track info")

                    if queue_text:
                        embed.add_field(
                            name="Up Next",
                            value="\n".join(queue_text),
                            inline=False
                        )

                    if len(queue) > 10:
                        embed.add_field(
                            name="And more...",
                            value=f"*{len(queue) - 10} more songs in queue*",
                            inline=False
                        )
                else:
                    embed.add_field(
                        name="Up Next",
                        value="*No songs in queue*",
                        inline=False
                    )
            except Exception as e:
                print(f"Error processing queue items: {e}")
                embed.add_field(
                    name="Up Next",
                    value="*Error displaying queue items*",
                    inline=False
                )

            if len(embed.fields) == 0:
                embed.add_field(
                    name="Queue Status",
                    value="No music is currently playing or queued",
                    inline=False
                )
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            print(f"Queue command error: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            
            error_embed = discord.Embed(
                title="Error Displaying Queue",
                description="An error occurred while trying to display the queue",
                color=discord.Color.red()
            )
            error_embed.add_field(
                name="Error Details",
                value=f"```{type(e).__name__}: {str(e)}```",
                inline=False
            )
            
            try:
                await interaction.followup.send(embed=error_embed)
            except:
                await interaction.followup.send("An error occurred while displaying the queue. Please try again.")

    @app_commands.command(name="pause", description="Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server!")
            return

        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message("Not playing any music!")
            return

        if not hasattr(voice_client, "is_playing") or not voice_client.is_playing():
            await interaction.response.send_message("No music is currently playing!")
            return

        if hasattr(voice_client, "is_paused") and voice_client.is_paused():
            await interaction.response.send_message("The music is already paused!")
            return

        if hasattr(voice_client, "pause"):
            voice_client.pause()
            await interaction.response.send_message("Paused the music!")
        else:
            await interaction.response.send_message("Unable to pause the music!")

    @app_commands.command(name="resume", description="Resume the current song")
    async def resume(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server!")
            return

        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message("Not playing any music!")
            return

        if hasattr(voice_client, "is_paused"):
            if not voice_client.is_paused():
                await interaction.response.send_message("The music is not paused!")
                return

            if hasattr(voice_client, "resume"):
                voice_client.resume()
                await interaction.response.send_message("Resumed the music!")
            else:
                await interaction.response.send_message("Unable to resume the music!")
        else:
            await interaction.response.send_message("Unable to check pause state!")

async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))