                # Provide workflow run link
                await self.client.send_message(
                    session['chat_id'],
                    f"🔗 **Workflow Monitor:**\n"
                    f"https://github.com/{GITHUB_REPO}/actions"
                )
            else:
                await progress_msg.edit(f"❌ **Failed to trigger workflow:**\n{message[:500]}")
            
            # Cleanup
            self.cleanup_user_session(user_id)
            
        except Exception as e:
            logger.error(f"Workflow processing error: {str(e)}")
            await progress_msg.edit(f"❌ **Error:** {str(e)[:500]}")
            self.cleanup_user_session(user_id)
    
    def get_free_port(self):
        """Get a free port number."""
        import socket
        sock = socket.socket()
        sock.bind(('', 0))
        port = sock.getsockname()[1]
        sock.close()
        return port
    
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
from telethon.tl.functions.messages import GetDocumentByHashRequest
from telethon.tl.types import InputDocumentFileLocation
from telethon.tl.types import InputFileLocation
from telethon.tl.types import Document, DocumentAttributeVideo
from telethon.tl.types import InputMediaUploadedDocument
from telethon.tl.types import InputDocument
from telethon.tl.types import InputFile
from telethon.tl import types
import time
import hashlib
import base64
from aiohttp import web
import re

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ⚠️ USER ACCOUNT - Telethon (for large files)
API_ID = int(os.getenv('API_ID', '123456'))
API_HASH = os.getenv('API_HASH', 'your_api_hash_here')
SESSION_STRING = os.getenv('SESSION_STRING', '')

if not SESSION_STRING:
    print("❌ ERROR: SESSION_STRING not set!")
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

class TelegramDirectDownload:
    """Generate direct download links for Telegram files (User Account)"""
    
    @staticmethod
    async def get_direct_download_link(client, message):
        """
        Get a direct download link for Telegram file using MTProto.
        This creates a temporary HTTP server to serve the file.
        """
        try:
            # Get file information
            if message.video:
                media = message.video
                mime_type = "video/mp4"
                file_ext = ".mp4"
            elif message.document:
                media = message.document
                mime_type = media.mime_type or "video/mp4"
                # Extract file extension
                file_ext = os.path.splitext(media.file_name or "video.mp4")[1]
                if not file_ext:
                    file_ext = ".mp4"
            else:
                return None, None
            
            # Get file reference details
            file_id = media.id
            access_hash = media.access_hash
            file_reference = media.file_reference
            
            # Construct a unique filename
            timestamp = int(time.time())
            file_hash = hashlib.md5(f"{file_id}{access_hash}{timestamp}".encode()).hexdigest()[:12]
            filename = f"video_{file_hash}{file_ext}"
            
            # For GitHub workflow, we need a public URL
            # Since we can't get Telegram's CDN URL directly, we'll use a different approach
            
            return None, filename  # We'll handle this differently
            
        except Exception as e:
            logger.error(f"Error getting download link: {str(e)}")
            return None, None
    
    @staticmethod
    async def create_temporary_download_server(client, message, port=9999):
        """
        Create a temporary HTTP server to serve the file.
        Returns: (server_url, stop_server_function)
        """
        from aiohttp import web
        import threading
        
        # Download file to memory or temp file
        temp_dir = Path("/tmp/telegram_files")
        temp_dir.mkdir(exist_ok=True)
        
        temp_file = temp_dir / f"temp_{int(time.time())}.mp4"
        
        # Download the file
        await client.download_media(message, file=temp_file)
        
        # Create simple HTTP server
        app = web.Application()
        
        async def handle_download(request):
            return web.FileResponse(temp_file)
        
        app.router.add_get('/download', handle_download)
        
        # Run server in background thread
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        
        # Get server IP (for Render, use the provided hostname)
        import socket
        hostname = socket.gethostname()
        
        # For Render, we need to use the service URL
        # Let's get Render's external URL if available
        render_service_url = os.getenv('RENDER_EXTERNAL_URL', f'http://{hostname}:{port}')
        
        download_url = f"{render_service_url}/download"
        
        def stop_server():
            asyncio.create_task(runner.cleanup())
            # Clean up temp file after some time
            asyncio.create_task(asyncio.sleep(60))
            if temp_file.exists():
                temp_file.unlink()
        
        return download_url, stop_server

class TelegramProxyDownloader:
    """
    Alternative: Use a proxy service that can download Telegram files
    using our session and provide a direct link.
    """
    
    @staticmethod
    def generate_proxy_download_url(file_info):
        """
        Generate a URL for a proxy service that will download the file.
        We'll encode the file info in the URL.
        """
        # Encode file info in base64
        file_data = {
            'file_id': file_info.get('id'),
            'access_hash': file_info.get('access_hash'),
            'dc_id': file_info.get('dc_id'),
            'size': file_info.get('size'),
            'mime_type': file_info.get('mime_type', 'video/mp4')
        }
        
        encoded_data = base64.urlsafe_b64encode(
            json.dumps(file_data).encode()
        ).decode()
        
        # In production, you'd use your own proxy server
        # For now, we'll return a placeholder
        return None

class GitHubWorkflowHandler:
    """Handler for triggering GitHub workflows"""
    
    @staticmethod
    async def trigger_workflow_with_telegram_file(client, user_id, speed, split_timestamps, youtube_title, github_title):
        """
        Main function: Get Telegram file, make it downloadable, trigger workflow
        """
        try:
            session = user_sessions[user_id]
            message = session['message']
            
            # Step 1: Get file information WITHOUT downloading
            file_info = await GitHubWorkflowHandler.get_file_info(message)
            
            if not file_info:
                return False, "❌ Could not get file information"
            
            # Step 2: Create a unique identifier for the file
            file_hash = hashlib.md5(
                f"{file_info['id']}{file_info['access_hash']}{int(time.time())}".encode()
            ).hexdigest()[:16]
            
            # Step 3: Store file metadata in GitHub repository (not the file itself)
            metadata_url = await GitHubWorkflowHandler.store_file_metadata(
                file_info, file_hash, youtube_title
            )
            
            if not metadata_url:
                return False, "❌ Failed to store file metadata"
            
            # Step 4: Trigger workflow with instructions to download from Telegram
            workflow_success = await GitHubWorkflowHandler.trigger_telegram_download_workflow(
                file_info, file_hash, speed, split_timestamps, youtube_title, github_title
            )
            
            if workflow_success:
                return True, f"""
✅ **Workflow triggered successfully!**

**Details:**
⚡ Speed: {speed}x
📺 YouTube Title: {youtube_title}
🐙 GitHub Release: {github_title}
🕒 Splits: {split_timestamps or 'No splits'}

**Processing started on GitHub Actions!**
The workflow will download the file directly from Telegram's servers.
                """
            else:
                return False, "❌ Failed to trigger workflow"
                
        except Exception as e:
            logger.error(f"Workflow trigger error: {str(e)}")
            return False, f"❌ Error: {str(e)[:500]}"
    
    @staticmethod
    async def get_file_info(message):
        """Extract file information from Telegram message."""
        try:
            if message.video:
                media = message.video
                file_name = "video.mp4"
            elif message.document:
                media = message.document
                file_name = media.file_name or "video.mp4"
            else:
                return None
            
            return {
                'id': media.id,
                'access_hash': media.access_hash,
                'file_reference': media.file_reference.hex() if media.file_reference else '',
                'size': media.size,
                'dc_id': media.dc_id,
                'mime_type': media.mime_type or 'video/mp4',
                'file_name': file_name,
                'chat_id': message.chat_id,
                'message_id': message.id
            }
        except Exception as e:
            logger.error(f"Error getting file info: {str(e)}")
            return None
    
    @staticmethod
    async def store_file_metadata(file_info, file_hash, title):
        """Store file metadata in GitHub repository."""
        try:
            repo = os.getenv("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")
            
            if not repo or not token:
                return None
            
            headers = {
                'Authorization': f'token {token}',
                'Accept': 'application/vnd.github.v3+json'
            }
            
            # Create metadata file
            metadata = {
                'file_info': file_info,
                'file_hash': file_hash,
                'title': title,
                'timestamp': int(time.time()),
                'expires_at': int(time.time()) + 3600  # 1 hour expiry
            }
            
            content = json.dumps(metadata, indent=2)
            content_b64 = base64.b64encode(content.encode()).decode()
            
            # Store in a temporary directory
            url = f"https://api.github.com/repos/{repo}/contents/temp_metadata/{file_hash}.json"
            
            data = {
                'message': f'Telegram file metadata: {title}',
                'content': content_b64,
                'branch': 'main'
            }
            
            response = requests.put(url, headers=headers, json=data)
            
            if response.status_code in [200, 201]:
                raw_url = f"https://raw.githubusercontent.com/{repo}/main/temp_metadata/{file_hash}.json"
                return raw_url
            else:
                logger.error(f"Failed to store metadata: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error storing metadata: {str(e)}")
            return None
    
    @staticmethod
    async def trigger_telegram_download_workflow(file_info, file_hash, speed, split_timestamps, youtube_title, github_title):
        """Trigger workflow that will download from Telegram."""
        try:
            repo = os.getenv("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")
            
            headers = {
                'Authorization': f'token {token}',
                'Accept': 'application/vnd.github.v3+json'
            }
            
            # Encode file info for workflow
            encoded_file_info = base64.urlsafe_b64encode(
                json.dumps(file_info).encode()
            ).decode()
            
            inputs = {
                'encoded_file_info': encoded_file_info,
                'file_hash': file_hash,
                'playback_speed': str(speed),
                'split_timestamps': split_timestamps or '',
                'release_name': github_title,
                'video_title': youtube_title
            }
            
            url = f'https://api.github.com/repos/{repo}/actions/workflows/telegram_download_processor.yml/dispatches'
            
            data = {
                'ref': 'main',
                'inputs': inputs
            }
            
            response = requests.post(url, headers=headers, json=data)
            
            return response.status_code == 204
            
        except Exception as e:
            logger.error(f"Error triggering workflow: {str(e)}")
            return False

class TelegramVideoBot:
    def __init__(self):
        self.client = TelegramClient(
            StringSession(SESSION_STRING),
            API_ID,
            API_HASH
        )
        self.me = None
    
    async def initialize(self):
        """Initialize Telegram client."""
        await self.client.start()
        self.me = await self.client.get_me()
        logger.info(f"Logged in as: @{self.me.username}")
    
    async def setup_handlers(self):
        """Setup event handlers."""
        
        @self.client.on(events.NewMessage(pattern='/start'))
        async def start_handler(event):
            """Handle /start command."""
            welcome = f"""
🎬 **Video Processing Bot** (User Account)

**Logged in as:** @{self.me.username}
**File limit:** 2GB (Free Telegram)

**Features:**
1. Send video file (up to 2GB)
2. Choose speed (0.5x to 3.0x)
3. Set split timestamps
4. Set YouTube & GitHub titles
5. Direct Telegram download in workflow

**How it works:**
1. Bot gets file info from Telegram
2. Sends metadata to GitHub
3. Workflow downloads directly from Telegram
4. No intermediate storage needed!

**Commands:**
/start - Start bot
/help - Show help
/status - Check status
/specs - System specifications

**Send a video to begin!**
            """
            await event.reply(welcome)
        
        @self.client.on(events.NewMessage(pattern='/specs'))
        async def specs_handler(event):
            """Handle /specs command."""
            import psutil
            
            cpu_count = psutil.cpu_count()
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            specs = f"""
🖥️ **SYSTEM SPECIFICATIONS**

💻 **CPU:**
• Cores: {cpu_count}
• Usage: {cpu_percent}%

🧠 **RAM:**
• Total: {memory.total/(1024**3):.2f} GB
• Used: {memory.used/(1024**3):.2f} GB ({memory.percent}%)
• Free: {memory.available/(1024**3):.2f} GB

💾 **DISK:**
• Total: {disk.total/(1024**3):.2f} GB
• Used: {disk.used/(1024**3):.2f} GB ({disk.percent}%)
• Free: {disk.free/(1024**3):.2f} GB

⚙️ **BOT:**
• Max File: {MAX_FILE_SIZE/(1024**3):.1f} GB
• Account: @{self.me.username}
• Mode: User Account (No 20MB limit!)
            """
            await event.reply(specs)
        
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
                
                # Store session
                user_sessions[user_id] = {
                    'message': event.message,
                    'file_name': file_name,
                    'file_size': media.size,
                    'chat_id': event.chat_id,
                    'timestamp': datetime.now(),
                    'step': 'speed',
                    'speed': None,
                    'split_timestamps': None,
                    'youtube_title': None,
                    'github_title': None
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
        
        @self.client.on(events.CallbackQuery())
        async def callback_handler(event):
            """Handle button callbacks."""
            user_id = event.sender_id
            data = event.data.decode() if event.data else ""
            
            try:
                if data == "cancel":
                    await event.edit("❌ **Operation cancelled.**")
                    if user_id in user_sessions:
                        del user_sessions[user_id]
                    return
                
                elif data.startswith("speed_"):
                    speed = float(data.split("_")[1])
                    
                    if user_id not in user_sessions:
                        await event.edit("❌ **Session expired!** Send video again.")
                        return
                    
                    user_sessions[user_id]['speed'] = speed
                    user_sessions[user_id]['step'] = 'split'
                    
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
                if user_id in user_sessions:
                    del user_sessions[user_id]
        
        @self.client.on(events.NewMessage)
        async def text_handler(event):
            """Handle text messages."""
            user_id = event.sender_id
            text = event.text.strip()
            
            if user_id not in user_sessions:
                return
            
            session = user_sessions[user_id]
            
            try:
                # Handle split timestamps
                if session.get('step') == 'split':
                    if text == '':
                        session['split_timestamps'] = ''
                        session['step'] = 'youtube_title'
                        await event.reply(
                            "✅ **No splits selected!**\n"
                            "**Step 3/4: Enter YouTube video title:**"
                        )
                    elif re.match(r'^(\d{1,2}:\d{2}:\d{2})(,\d{1,2}:\d{2}:\d{2})*$', text):
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
                    
                    # Start workflow processing
                    await self.start_workflow_processing(user_id, event)
                    
            except Exception as e:
                logger.error(f"Text handler error: {str(e)}")
                await event.reply(f"❌ Error: {str(e)[:200]}")
                if user_id in user_sessions:
                    del user_sessions[user_id]
    
    async def start_workflow_processing(self, user_id, event):
        """Start workflow processing."""
        try:
            session = user_sessions[user_id]
            
            # Send initial message
            progress_msg = await event.reply("⚙️ **Getting file information from Telegram...**")
            
            # Trigger workflow with Telegram file info
            success, message = await GitHubWorkflowHandler.trigger_workflow_with_telegram_file(
                self.client, user_id,
                session['speed'],
                session.get('split_timestamps', ''),
                session['youtube_title'],
                session['github_title']
            )
            
            if success:
                await progress_msg.edit(message)
            else:
                await progress_msg.edit(f"❌ **Error:** {message[:500]}")
            
            # Cleanup
            if user_id in user_sessions:
                del user_sessions[user_id]
                
        except Exception as e:
            logger.error(f"Workflow processing error: {str(e)}")
            await event.reply(f"❌ **Error:** {str(e)[:500]}")
            if user_id in user_sessions:
                del user_sessions[user_id]
    
    async def run(self):
        """Run the bot."""
        print("\n" + "="*60)
        print("👤 TELEGRAM USER ACCOUNT BOT")
        print(f"📁 Max file size: {MAX_FILE_SIZE/(1024**3):.1f}GB")
        print(f"🌐 Web server port: {PORT}")
        print("="*60)
        
        await self.initialize()
        await self.setup_handlers()
        
        print(f"✅ Logged in as: @{self.me.username}")
        print(f"✅ User ID: {self.me.id}")
        print("✅ Bot is ready!")
        print("💬 Send videos to this account")
        print("="*60)
        
        await self.client.run_until_disconnected()
    
    async def stop(self):
        """Stop the bot."""
        await self.client.disconnect()

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
                        <li>Process videos up to 4GB</li>
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
        await app['bot'].stop()

async def main():
    """Main function to start both web server and bot."""
    # Check dependencies
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        print("✅ FFmpeg is installed")
    except:
        print("❌ FFmpeg not found! Installing...")
        subprocess.run(['apt-get', 'update'], capture_output=True)
        subprocess.run(['apt-get', 'install', '-y', 'ffmpeg'], capture_output=True)
    
    # Check Python dependencies
    try:
        import nacl
        print("✅ PyNaCl is installed")
    except ImportError:
        print("❌ PyNaCl not found! Installing...")
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'pynacl'])
    
    try:
        import psutil
        print("✅ psutil is installed")
    except ImportError:
        print("❌ psutil not found! Installing...")
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'psutil'])
    
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
