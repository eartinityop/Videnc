import os
import sys
import asyncio
import logging
import tempfile
import shutil
import json
import requests
import subprocess
import psutil
from pathlib import Path
from datetime import datetime
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from aiohttp import web
import re
import base64
import time
import hashlib

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ⚠️ SET THESE IN ENVIRONMENT VARIABLES ⚠️
API_ID = int(os.getenv('API_ID', '123456'))
API_HASH = os.getenv('API_HASH', 'your_api_hash_here')
SESSION_STRING = os.getenv('SESSION_STRING', '')
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')
GITHUB_REPO = os.getenv('GITHUB_REPO', '')  # Format: username/repo
YOUTUBE_CLIENT_ID = os.getenv('YOUTUBE_CLIENT_ID', '')
YOUTUBE_CLIENT_SECRET = os.getenv('YOUTUBE_CLIENT_SECRET', '')

if not SESSION_STRING:
    print("❌ ERROR: SESSION_STRING not set!")
    print("Run generate_session.py locally to get session string")
    sys.exit(1)

# Configuration
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB for free Telegram
PORT = int(os.getenv('PORT', 10000))

# Speed options
SPEED_OPTIONS = [
    [Button.inline("0.5x", b"speed_0.5"), Button.inline("0.75x", b"speed_0.75")],
    [Button.inline("1.25x", b"speed_1.25"), Button.inline("1.3x", b"speed_1.3")],
    [Button.inline("1.4x", b"speed_1.4"), Button.inline("1.5x", b"speed_1.5")],
    [Button.inline("2.0x", b"speed_2.0"), Button.inline("3.0x", b"speed_3.0")],
    [Button.inline("❌ Cancel", b"cancel")]
]

# Store user sessions
user_sessions = {}

class VideoProcessor:
    @staticmethod
    async def process_video(input_path, output_path, speed_factor):
        """Process video with FFmpeg."""
        try:
            # Create audio filter
            def create_audio_filter(speed):
                if speed > 2.0:
                    atempo_filters = []
                    remaining = speed
                    while remaining > 2.0:
                        atempo_filters.append("atempo=2.0")
                        remaining /= 2.0
                    atempo_filters.append(f"atempo={remaining:.2f}")
                    return ",".join(atempo_filters)
                elif speed < 0.5:
                    atempo_filters = []
                    remaining = speed
                    while remaining < 0.5:
                        atempo_filters.append("atempo=0.5")
                        remaining *= 2.0
                    atempo_filters.append(f"atempo={remaining:.2f}")
                    return ",".join(atempo_filters)
                else:
                    return f"atempo={speed}"
            
            audio_filter = create_audio_filter(speed_factor)
            video_filter = f"setpts={1/speed_factor:.5f}*PTS"
            
            # Build FFmpeg command
            cmd = [
                'ffmpeg', '-i', input_path,
                '-filter_complex', f'[0:v]{video_filter}[v];[0:a]{audio_filter}[a]',
                '-map', '[v]', '-map', '[a]',
                '-c:v', 'libx264', '-preset', 'medium',
                '-crf', '23', '-c:a', 'aac',
                '-b:a', '192k', '-movflags', '+faststart',
                '-y', output_path
            ]
            
            logger.info(f"Running FFmpeg: {' '.join(cmd)}")
            
            # Run FFmpeg
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                raise Exception(f"FFmpeg failed: {error_msg[:200]}")
            
            return True
            
        except Exception as e:
            logger.error(f"Processing error: {str(e)}")
            raise

class GitHubWorkflowHandler:
    @staticmethod
    async def trigger_workflow(video_url, playback_speed, split_timestamps, release_name, video_title):
        """Trigger GitHub workflow with given parameters."""
        try:
            headers = {
                'Authorization': f'token {GITHUB_TOKEN}',
                'Accept': 'application/vnd.github.v3+json'
            }
            
            # Prepare workflow inputs
            inputs = {
                'video_url': video_url,
                'playback_speed': str(playback_speed),
                'split_timestamps': split_timestamps or '',
                'release_name': release_name,
                'video_title': video_title
            }
            
            # Trigger workflow
            url = f'https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/video_processor.yml/dispatches'
            
            data = {
                'ref': 'main',  # or your default branch
                'inputs': inputs
            }
            
            response = requests.post(url, headers=headers, json=data)
            
            if response.status_code == 204:
                return True, "✅ Workflow triggered successfully!"
            else:
                return False, f"❌ Failed to trigger workflow: {response.status_code} - {response.text}"
                
        except Exception as e:
            logger.error(f"GitHub workflow error: {str(e)}")
            return False, str(e)
    
    @staticmethod
    def update_youtube_token(refresh_token):
        """Update YouTube refresh token in GitHub secrets."""
        try:
            # Get public key for secrets
            headers = {
                'Authorization': f'token {GITHUB_TOKEN}',
                'Accept': 'application/vnd.github.v3+json'
            }
            
            # Get public key
            pk_url = f'https://api.github.com/repos/{GITHUB_REPO}/actions/secrets/public-key'
            pk_response = requests.get(pk_url, headers=headers)
            
            if pk_response.status_code != 200:
                return False
            
            pk_data = pk_response.json()
            key_id = pk_data['key_id']
            public_key = pk_data['key']
            
            # Encrypt the secret using libsodium
            from nacl.public import PublicKey, SealedBox
            public_key_obj = PublicKey(base64.b64decode(public_key))
            sealed_box = SealedBox(public_key_obj)
            encrypted = sealed_box.encrypt(refresh_token.encode())
            encrypted_b64 = base64.b64encode(encrypted).decode()
            
            # Update secret
            secret_url = f'https://api.github.com/repos/{GITHUB_REPO}/actions/secrets/YOUTUBE_REFRESH_TOKEN'
            data = {
                'encrypted_value': encrypted_b64,
                'key_id': key_id
            }
            
            response = requests.put(secret_url, headers=headers, json=data)
            
            return response.status_code == 204
            
        except Exception as e:
            logger.error(f"Failed to update YouTube token: {str(e)}")
            return False

class YouTubeAuthHandler:
    @staticmethod
    def get_auth_url():
        """Generate Google OAuth URL."""
        scope = "https://www.googleapis.com/auth/youtube.upload"
        client_id = YOUTUBE_CLIENT_ID
        redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        
        auth_url = f"https://accounts.google.com/o/oauth2/auth"
        auth_url += f"?client_id={client_id}"
        auth_url += f"&redirect_uri={redirect_uri}"
        auth_url += f"&scope={scope}"
        auth_url += "&response_type=code"
        auth_url += "&access_type=offline"
        auth_url += "&prompt=consent"
        
        return auth_url
    
    @staticmethod
    def exchange_code_for_token(authorization_code):
        """Exchange authorization code for refresh token."""
        try:
            token_url = "https://oauth2.googleapis.com/token"
            
            data = {
                'client_id': YOUTUBE_CLIENT_ID,
                'client_secret': YOUTUBE_CLIENT_SECRET,
                'code': authorization_code,
                'grant_type': 'authorization_code',
                'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob'
            }
            
            response = requests.post(token_url, data=data)
            response_data = response.json()
            
            if 'refresh_token' in response_data:
                return True, response_data['refresh_token']
            else:
                return False, response_data.get('error_description', 'No refresh token received')
                
        except Exception as e:
            logger.error(f"Token exchange error: {str(e)}")
            return False, str(e)

class SystemMonitor:
    @staticmethod
    def get_system_specs():
        """Get system specifications."""
        try:
            # CPU Info
            cpu_count = psutil.cpu_count()
            cpu_percent = psutil.cpu_percent(interval=1)
            
            # Memory Info
            memory = psutil.virtual_memory()
            total_ram_gb = memory.total / (1024**3)
            used_ram_gb = memory.used / (1024**3)
            free_ram_gb = memory.available / (1024**3)
            ram_percent = memory.percent
            
            # Disk Info
            disk = psutil.disk_usage('/')
            total_disk_gb = disk.total / (1024**3)
            used_disk_gb = disk.used / (1024**3)
            free_disk_gb = disk.free / (1024**3)
            disk_percent = disk.percent
            
            # System Info
            boot_time = datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S")
            
            # Network Info
            net_io = psutil.net_io_counters()
            
            specs = f"""
**🖥️ SYSTEM SPECIFICATIONS**

**💻 CPU:**
• Cores: {cpu_count}
• Usage: {cpu_percent}%

**🧠 RAM:**
• Total: {total_ram_gb:.2f} GB
• Used: {used_ram_gb:.2f} GB ({ram_percent}%)
• Free: {free_ram_gb:.2f} GB

**💾 DISK:**
• Total: {total_disk_gb:.2f} GB
• Used: {used_disk_gb:.2f} GB ({disk_percent}%)
• Free: {free_disk_gb:.2f} GB

**🌐 NETWORK:**
• Sent: {net_io.bytes_sent / (1024**2):.2f} MB
• Received: {net_io.bytes_recv / (1024**2):.2f} MB

**📊 SYSTEM:**
• Boot Time: {boot_time}
• Max File Size: {MAX_FILE_SIZE/(1024**3):.1f} GB

**⚙️ BOT INFO:**
• GitHub Repo: {GITHUB_REPO or 'Not set'}
• YouTube Auth: {'✅ Configured' if YOUTUBE_CLIENT_ID else '❌ Not configured'}
            """
            
            return specs
            
        except Exception as e:
            logger.error(f"Error getting system specs: {str(e)}")
            return f"❌ Error getting system specs: {str(e)}"

class TelegramVideoBot:
    def __init__(self):
        self.client = TelegramClient(
            StringSession(SESSION_STRING),
            API_ID,
            API_HASH
        )
        self.me = None
    
    async def start(self):
        """Start the Telegram bot."""
        print("\n" + "="*50)
        print("🎬 TELEGRAM VIDEO SPEED BOT")
        print(f"📁 Max file size: {MAX_FILE_SIZE/(1024**3):.1f}GB")
        print(f"🌐 Web server port: {PORT}")
        print("="*50)
        
        # Connect with session string
        await self.client.start()
        self.me = await self.client.get_me()
        
        # Setup handlers
        await self.setup_handlers()
        
        print(f"✅ Logged in as: @{self.me.username}")
        print(f"✅ User ID: {self.me.id}")
        print("✅ Bot is ready!")
        print("💬 Send videos to this account")
        print("="*50)
        
        # Keep running
        await self.client.run_until_disconnected()
    
    async def setup_handlers(self):
        """Setup all event handlers."""
        
        @self.client.on(events.NewMessage(pattern='/start'))
        async def start_handler(event):
            """Handle /start command."""
            if not self.me:
                self.me = await self.client.get_me()
            
            welcome = f"""
🎬 **Video Processing Bot**

**Logged in as:** @{self.me.username}
**File limit:** 2GB (Free) / 4GB (Premium)

**Features:**
1. Send video file
2. Choose processing speed
3. Set split timestamps
4. Set YouTube & GitHub titles
5. Upload to YouTube & GitHub Releases

**Commands:**
/start - Start bot
/help - Show help
/status - Check status
/specs - System specifications
/auth_youtube - Setup YouTube upload
/workflow_status - Check GitHub workflow

**Send a video to begin!**
            """
            await event.reply(welcome)
        
        @self.client.on(events.NewMessage(pattern='/help'))
        async def help_handler(event):
            """Handle /help command."""
            help_text = """
**Commands:**
/start - Start bot
/help - This message
/status - Check bot status
/specs - System specifications
/auth_youtube - Setup YouTube upload
/workflow_status - Check GitHub workflow

**Process Flow:**
1. Send video file
2. Choose speed (buttons)
3. Enter split timestamps (HH:MM:SS,HH:MM:SS)
4. Enter YouTube title
5. Enter GitHub release title
6. Bot downloads and uploads to GitHub
7. Bot triggers GitHub workflow

**Split Format:** 01:30:00,02:45:00,03:15:00

**Speed Options:**
0.5x, 0.75x, 1.25x, 1.3x, 1.4x, 1.5x, 2.0x, 3.0x

**YouTube Auth:**
Use /auth_youtube to setup automatic uploads
            """
            await event.reply(help_text)
        
        @self.client.on(events.NewMessage(pattern='/specs'))
        async def specs_handler(event):
            """Handle /specs command."""
            specs = SystemMonitor.get_system_specs()
            await event.reply(specs)
        
        @self.client.on(events.NewMessage(pattern='/auth_youtube'))
        async def auth_youtube_handler(event):
            """Handle YouTube authentication."""
            user_id = event.sender_id
            
            try:
                if not YOUTUBE_CLIENT_ID or not YOUTUBE_CLIENT_SECRET:
                    await event.reply("❌ **YouTube credentials not configured!**\n"
                                    "Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET environment variables.")
                    return
                
                auth_url = YouTubeAuthHandler.get_auth_url()
                
                message = await event.reply(
                    f"**YouTube Authentication Setup**\n\n"
                    f"1. Click this link: {auth_url}\n"
                    f"2. Select your Google account\n"
                    f"3. Copy the authorization code\n"
                    f"4. Send it here\n\n"
                    f"⚠️ This will allow automatic uploads to your YouTube account\n"
                    f"⏱️ Token is valid for 7 days",
                    link_preview=False
                )
                
                # Store that user is waiting for auth code
                if user_id not in user_sessions:
                    user_sessions[user_id] = {}
                user_sessions[user_id]['waiting_for_auth'] = True
                user_sessions[user_id]['auth_message_id'] = message.id
                
            except Exception as e:
                await event.reply(f"❌ Error setting up auth: {str(e)[:200]}")
        
        @self.client.on(events.NewMessage(pattern='/workflow_status'))
        async def workflow_status_handler(event):
            """Check GitHub workflow status."""
            try:
                if not GITHUB_TOKEN or not GITHUB_REPO:
                    await event.reply("❌ GitHub credentials not configured!")
                    return
                
                headers = {
                    'Authorization': f'token {GITHUB_TOKEN}',
                    'Accept': 'application/vnd.github.v3+json'
                }
                
                url = f'https://api.github.com/repos/{GITHUB_REPO}/actions/runs'
                response = requests.get(url, headers=headers)
                
                if response.status_code == 200:
                    runs = response.json()['workflow_runs'][:5]  # Last 5 runs
                    
                    status_text = "**Recent Workflow Runs:**\n\n"
                    for run in runs:
                        status_emoji = {
                            'completed': '✅',
                            'in_progress': '🔄',
                            'queued': '⏳',
                            'action_required': '⚠️',
                            'cancelled': '❌',
                            'failure': '❌'
                        }.get(run['status'], '❓')
                        
                        conclusion_emoji = {
                            'success': '✅',
                            'failure': '❌',
                            'cancelled': '⏹️',
                            'skipped': '⏭️',
                            'neutral': '⚪'
                        }.get(run.get('conclusion'), '❓')
                        
                        status_text += (
                            f"{status_emoji} **Run #{run['run_number']}**\n"
                            f"Status: {run['status']} {conclusion_emoji}\n"
                            f"Created: {run['created_at'][:19].replace('T', ' ')}\n"
                            f"Branch: {run['head_branch']}\n\n"
                        )
                    
                    await event.reply(status_text)
                else:
                    await event.reply(f"❌ Failed to fetch workflow status: {response.text}")
                    
            except Exception as e:
                await event.reply(f"❌ Error: {str(e)[:200]}")
        
        @self.client.on(events.NewMessage(pattern='/status'))
        async def status_handler(event):
            """Handle /status command."""
            import psutil
            
            disk = psutil.disk_usage('/')
            memory = psutil.virtual_memory()
            
            status = f"""
**🤖 BOT STATUS**

**👤 Account:** @{self.me.username if self.me else 'Loading...'}
**🔄 Active sessions:** {len(user_sessions)}
**💾 Free disk:** {disk.free/(1024**3):.1f}GB
**📁 Max file size:** {MAX_FILE_SIZE/(1024**3):.1f}GB

**⚙️ SYSTEM:**
• CPU Usage: {psutil.cpu_percent()}%
• RAM Usage: {memory.percent}%
• Disk Usage: {disk.percent}%

**🔧 CONFIGURATION:**
• GitHub Repo: {GITHUB_REPO or 'Not set'}
• YouTube Auth: {'✅ Configured' if YOUTUBE_CLIENT_ID else '❌ Not configured'}

**✅ Bot is ready to process videos!**
            """
            await event.reply(status)
        
        @self.client.on(events.NewMessage(
            func=lambda e: e.video or (
                e.document and e.document.mime_type and 
                'video' in str(e.document.mime_type).lower()
            )
        ))
        async def video_handler(event):
            """Handle incoming videos."""
            user_id = event.sender_id
            
            try:
                # Get video info
                if event.video:
                    media = event.video
                    file_name = "video.mp4"
                else:
                    media = event.document
                    file_name = media.file_name or "video.mp4"
                
                # Check file size
                if media.size > MAX_FILE_SIZE:
                    max_gb = MAX_FILE_SIZE / (1024**3)
                    file_gb = media.size / (1024**3)
                    await event.reply(f"❌ **File too large!**\nYour file: {file_gb:.1f}GB\nMax: {max_gb:.1f}GB")
                    return
                
                # Create temp directory
                temp_dir = Path(f"/tmp/temp_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
                temp_dir.mkdir(parents=True, exist_ok=True)
                
                # Store session
                user_sessions[user_id] = {
                    'media': media,
                    'file_name': fille_name,
                    'temp_dir': temp_dir,
                    'chat_id': event.chat_id,
                    'timestamp': datetime.now(),
                    'step': 'speed',  # Start with speed selection
                    'speed': None,
                    'split_timestamps': None,
                    'youtube_title': None,
                    'github_title': None,
                    'video_url': None
                }
                
                # Send speed selection buttons
                file_size_mb = media.size / (1024*1024)
                await event.reply(
                    f"✅ **Video received!**\n"
                    f"Size: {file_size_mb:.1f}MB\n"
                    f"**Step 1/4: Choose playback speed:**",
                    buttons=SPEED_OPTIONS
                )
                
            except Exception as e:
                logger.error(f"Video handler error: {str(e)}")
                await event.reply(f"❌ Error: {str(e)[:200]}")
        
        @self.client.on(events.NewMessage)
        async def text_handler(event):
            """Handle text messages for workflow inputs."""
            user_id = event.sender_id
            text = event.text.strip()
            
            if user_id not in user_sessions:
                return
            
            session = user_sessions[user_id]
            
            try:
                # Handle YouTube auth code
                if session.get('waiting_for_auth') and len(text) > 20 and ' ' not in text:
                    success, result = YouTubeAuthHandler.exchange_code_for_token(text)
                    
                    if success:
                        # Update GitHub secret
                        if GitHubWorkflowHandler.update_youtube_token(result):
                            await event.reply("✅ **YouTube token updated successfully!**\n"
                                            "Your videos will now upload to YouTube automatically.")
                        else:
                            await event.reply("✅ **Token received but failed to update GitHub.**\n"
                                            "Manual update required.")
                    else:
                        await event.reply(f"❌ **Token exchange failed:**\n{result}")
                    
                    session['waiting_for_auth'] = False
                    return
                
                # Handle split timestamps
                if session.get('step') == 'split':
                    # Allow empty string for no splits
                    if text == '':
                        session['split_timestamps'] = ''
                        session['step'] = 'youtube_title'
                        await event.reply(
                            "✅ **No splits selected!**\n"
                            "**Step 3/4: Enter YouTube video title:**"
                        )
                    elif self.validate_timestamps(text):
                        session['split_timestamps'] = text
                        session['step'] = 'youtube_title'
                        await event.reply(
                            "✅ **Split timestamps saved!**\n"
                            "**Step 3/4: Enter YouTube video title:**"
                        )
                    else:
                        await event.reply(
                            "❌ **Invalid format!**\n"
                            "Please enter timestamps in HH:MM:SS format separated by commas.\n"
                            "Example: 01:30:00,02:45:00,03:15:00\n"
                            "Or press Enter for no splits\n"
                            "Enter split timestamps again:"
                        )
                
                # Handle YouTube title
                elif session.get('step') == 'youtube_title':
                    if len(text) < 5:
                        await event.reply("❌ **Title too short!** Please enter a valid YouTube title (min 5 characters):")
                        return
                    
                    session['youtube_title'] = text
                    session['step'] = 'github_title'
                    await event.reply(
                        "✅ **YouTube title saved!**\n"
                        "**Step 4/4: Enter GitHub release title:**"
                    )
                
                # Handle GitHub title
                elif session.get('step') == 'github_title':
                    if len(text) < 3:
                        await event.reply("❌ **Title too short!** Please enter a valid GitHub release title (min 3 characters):")
                        return
                    
                    session['github_title'] = text
                    
                    # All data collected, start processing
                    await self.start_workflow_processing(user_id, event)
                    
            except Exception as e:
                logger.error(f"Text handler error: {str(e)}")
                await event.reply(f"❌ Error: {str(e)[:200]}")
        
        @self.client.on(events.CallbackQuery())
        async def callback_handler(event):
            """Handle button callbacks."""
            user_id = event.sender_id
            data = event.data.decode() if event.data else ""
            
            try:
                if data == "cancel":
                    await event.edit("❌ **Operation cancelled.**")
                    self.cleanup_user_session(user_id)
                    return
                
                elif data.startswith("speed_"):
                    speed = float(data.split("_")[1])
                    
                    if user_id not in user_sessions:
                        await event.edit("❌ **Session expired!** Send video again.")
                        return
                    
                    session = user_sessions[user_id]
                    session['speed'] = speed
                    session['step'] = 'split'
                    
                    await event.edit(
                        f"✅ **Speed selected:** {speed}x\n"
                        f"**Step 2/4: Enter split timestamps (HH:MM:SS,HH:MM:SS)**\n"
                        f"Example: 01:00:00,02:00:00\n"
                        f"Or press Enter for no splits:"
                    )
                
            except Exception as e:
                logger.error(f"Callback error: {str(e)}")
                try:
                    await event.edit(f"❌ Error: {str(e)[:200]}")
                except:
                    pass
                self.cleanup_user_session(user_id)
    
    def validate_timestamps(self, timestamps):
        """Validate HH:MM:SS format."""
        if not timestamps:
            return True
        
        pattern = r'^(\d{1,2}:\d{2}:\d{2})(,\d{1,2}:\d{2}:\d{2})*$'
        if re.match(pattern, timestamps):
            # Validate each timestamp
            parts = timestamps.split(',')
            for part in parts:
                h, m, s = map(int, part.split(':'))
                if h > 23 or m > 59 or s > 59:
                    return False
            return True
        return False
    
    async def start_workflow_processing(self, user_id, event):
        """Start processing and trigger GitHub workflow."""
        try:
            session = user_sessions[user_id]
            
            # Create progress message
            progress_msg = await event.reply("⚙️ **Starting processing...**")
            
            # Step 1: Download video to bot server
            await progress_msg.edit("📥 **Downloading video from Telegram...**")
            
            temp_dir = session['temp_dir']
            input_path = temp_dir / "original_video.mp4"
            
            last_percent = -5
            
            def progress_callback(current, total):
                nonlocal last_percent
                percent = (current / total) * 100
                if percent - last_percent >= 5 or current == total:
                    asyncio.create_task(
                        progress_msg.edit(
                            f"📥 **Downloading...**\n"
                            f"Progress: {percent:.1f}%\n"
                            f"Size: {current/(1024*1024):.1f}MB / {total/(1024*1024):.1f}MB"
                        )
                    )
                    last_percent = percent
            
            await self.client.download_media(
                session['media'],
                file=input_path,
                progress_callback=progress_callback
            )
            
            # Step 2: Upload to transfer.sh for temporary storage
            await progress_msg.edit("☁️ **Uploading to temporary storage...**")
            
            upload_url = await self.upload_to_transfersh(input_path, session['file_name'])
            
            if not upload_url:
                # Try alternative: file.io
                upload_url = await self.upload_to_fileio(input_path)
            
            if not upload_url:
                # Last resort: Upload to GitHub as a gist or file
                upload_url = await self.upload_to_github_temp(input_path, session['file_name'])
            
            if not upload_url:
                raise Exception("Failed to upload video to temporary storage")
            
            session['video_url'] = upload_url
            
            # Step 3: Trigger GitHub workflow
            await progress_msg.edit("🚀 **Triggering GitHub workflow...**")
            
            success, message = await GitHubWorkflowHandler.trigger_workflow(
                video_url=upload_url,
                playback_speed=session['speed'],
                split_timestamps=session.get('split_timestamps', ''),
                release_name=session['github_title'],
                video_title=session['youtube_title']
            )
            
            if success:
                await progress_msg.edit(
                    f"✅ **Workflow triggered successfully!**\n\n"
                    f"**Details:**\n"
                    f"• Speed: {session['speed']}x\n"
                    f"• YouTube Title: {session['youtube_title']}\n"
                    f"• GitHub Release: {session['github_title']}\n"
                    f"• Splits: {session.get('split_timestamps', 'No splits')}\n\n"
                    f"📊 Check workflow status with /workflow_status\n"
                    f"📺 Videos will be uploaded to YouTube and GitHub Releases."
                )
            else:
                await progress_msg.edit(f"❌ **Failed to trigger workflow:**\n{message}")
            
            # Cleanup
            self.cleanup_user_session(user_id)
            
        except Exception as e:
            logger.error(f"Workflow processing error: {str(e)}")
            await progress_msg.edit(f"❌ **Error:** {str(e)[:500]}")
            self.cleanup_user_session(user_id)
    
    async def upload_to_transfersh(self, file_path, filename):
        """Upload file to transfer.sh for temporary storage."""
        try:
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                with open(file_path, 'rb') as f:
                    data = aiohttp.FormData()
                    data.add_field('file', f, filename=filename)
                    
                    async with session.post('https://transfer.sh', data=data) as response:
                        if response.status == 200:
                            return (await response.text()).strip()
            return None
            
        except Exception as e:
            logger.error(f"transfer.sh upload error: {str(e)}")
            return None
    
    async def upload_to_fileio(self, file_path):
        """Upload file to file.io (max 2GB, 14 days)."""
        try:
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                with open(file_path, 'rb') as f:
                    data = aiohttp.FormData()
                    data.add_field('file', f, filename=os.path.basename(file_path))
                    
                    async with session.post('https://file.io', data=data) as response:
                        if response.status == 200:
                            result = await response.json()
                            if result.get('success'):
                                return result['link']
            return None
            
        except Exception as e:
            logger.error(f"file.io upload error: {str(e)}")
            return None
    
    async def upload_to_github_temp(self, file_path, filename):
        """Upload file to GitHub as a temporary file."""
        try:
            if not GITHUB_TOKEN or not GITHUB_REPO:
                return None
            
            # Read file content
            with open(file_path, 'rb') as f:
                content = f.read()
            
            # Encode to base64
            content_b64 = base64.b64encode(content).decode()
            
            # Create a unique filename
            file_hash = hashlib.md5(content).hexdigest()[:12]
            unique_filename = f"video_{file_hash}.mp4"
            
            headers = {
                'Authorization': f'token {GITHUB_TOKEN}',
                'Accept': 'application/vnd.github.v3+json'
            }
            
            # Upload to a temporary directory in the repo
            url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/temp_uploads/{unique_filename}"
            
            data = {
                'message': f'Upload video for processing: {filename}',
                'content': content_b64,
                'branch': 'main'
            }
            
            response = requests.put(url, headers=headers, json=data)
            
            if response.status_code in [200, 201]:
                return f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/temp_uploads/{unique_filename}"
            else:
                logger.error(f"GitHub upload failed: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"GitHub upload error: {str(e)}")
            return None
    
    def cleanup_user_session(self, user_id):
        """Clean up user's temporary files."""
        try:
            if user_id in user_sessions:
                session = user_sessions[user_id]
                temp_dir = session.get('temp_dir')
                
                if temp_dir and temp_dir.exists():
                    # Try to remove directory
                    try:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                    except:
                        pass
                
                del user_sessions[user_id]
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

# Web server functions
async def handle_health(request):
    """Health check endpoint."""
    return web.Response(text="✅ Bot is running!")

async def handle_root(request):
    """Root endpoint."""
    html = """
    <html>
        <head>
            <title>Telegram Video Processing Bot</title>
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                .container { max-width: 800px; margin: 0 auto; }
                .status { color: green; font-weight: bold; }
                .features { text-align: left; margin: 20px 0; }
                .command { background: #f0f0f0; padding: 10px; border-radius: 5px; margin: 5px 0; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🎬 Telegram Video Processing Bot</h1>
                <p class="status">✅ Bot is running and ready!</p>
                <div class="features">
                    <h3>Features:</h3>
                    <ul>
                        <li>Process videos up to 2GB</li>
                        <li>Speed adjustment (0.5x to 3.0x)</li>
                        <li>Split videos at timestamps</li>
                        <li>Automatic YouTube upload</li>
                        <li>GitHub Releases upload</li>
                        <li>GitHub Actions workflow automation</li>
                    </ul>
                    <h3>Available Commands:</h3>
                    <div class="command">/start - Start the bot</div>
                    <div class="command">/help - Show help message</div>
                    <div class="command">/status - Check bot status</div>
                    <div class="command">/specs - System specifications</div>
                    <div class="command">/auth_youtube - Setup YouTube upload</div>
                    <div class="command">/workflow_status - Check GitHub workflow</div>
                </div>
                <p>Find it on Telegram by searching for your account username.</p>
                <p><a href="/health">Health Check</a></p>
            </div>
        </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def start_bot(app):
    """Start the Telegram bot in background."""
    bot = TelegramVideoBot()
    app['bot'] = bot
    # Start bot in background
    asyncio.create_task(bot.start())

async def cleanup_bot(app):
    """Cleanup bot on shutdown."""
    if 'bot' in app:
        bot = app['bot']
        await bot.client.disconnect()

async def main():
    """Main function to start both web server and bot."""
    # Check dependencies
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        print("✅ FFmpeg is installed")
    except:
        print("❌ FFmpeg not found!")
    
    # Check Python dependencies
    try:
        import nacl
        print("✅ PyNaCl is installed")
    except ImportError:
        print("❌ PyNaCl not found!")
    
    try:
        import psutil
        print("✅ psutil is installed")
    except ImportError:
        print("❌ psutil not found!")
    
    # Create web application
    app = web.Application()
    
    # Add routes
    app.router.add_get('/', handle_root)
    app.router.add_get('/health', handle_health)
    
    # Add startup and cleanup callbacks
    app.on_startup.append(start_bot)
    app.on_cleanup.append(cleanup_bot)
    
    # Create temp directory
    Path("/tmp/videos").mkdir(parents=True, exist_ok=True)
    
    # Start web server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    
    print(f"🌐 Starting web server on port {PORT}...")
    await site.start()
    
    print("✅ Web server started!")
    print("📡 Bot is running in background")
    print("🛑 Send SIGINT (Ctrl+C) to stop")
    
    # Keep running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
    finally:
        await runner.cleanup()

if __name__ == '__main__':
    # Run the application
    asyncio.run(main())
