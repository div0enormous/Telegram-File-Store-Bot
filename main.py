import sqlite3, base64
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, ForceReply
from configbck import API_ID, API_HASH, BOT_TOKEN, DB_CHANNEL, ADMINS
import asyncio
from datetime import datetime, timedelta
import re
import logging
from pyrogram.errors import FloodWait, RPCError
import socket

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Bot setup with auto-reconnect settings
bot = Client(
    "FileStoreBot", 
    api_id=API_ID, 
    api_hash=API_HASH, 
    bot_token=BOT_TOKEN,
    max_concurrent_transmissions=3,
    no_updates=False
)

# SQLite setup
conn = sqlite3.connect("files.db", check_same_thread=False)
cursor = conn.cursor()

# Enhanced Tables
cursor.execute("""CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    message_id INTEGER,
    file_name TEXT,
    file_type TEXT,
    file_size INTEGER,
    uploaded_by INTEGER,
    upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    delete_after_minutes INTEGER DEFAULT 0,
    expiry_time TIMESTAMP
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_name TEXT,
    start_msg INTEGER,
    end_msg INTEGER,
    created_by INTEGER,
    created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    delete_after_minutes INTEGER DEFAULT 0,
    expiry_time TIMESTAMP
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    username TEXT,
    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_banned INTEGER DEFAULT 0
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS batch_upload_sessions (
    session_id TEXT PRIMARY KEY,
    admin_id INTEGER,
    batch_name TEXT,
    start_msg_id INTEGER,
    status TEXT DEFAULT 'waiting_end',
    delete_after_minutes INTEGER DEFAULT 0
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS search_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    keywords TEXT,
    post_chat_id INTEGER,
    post_message_id INTEGER,
    added_by INTEGER,
    added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS broadcast_state (
    admin_id INTEGER PRIMARY KEY,
    message TEXT,
    total_users INTEGER
)""")

conn.commit()

# Global state management
pending_actions = {}

# Helper Functions
def encode_payload(payload: str) -> str:
    return base64.urlsafe_b64encode(payload.encode()).decode()

def decode_payload(payload: str) -> str:
    try:
        return base64.urlsafe_b64decode(payload.encode()).decode()
    except:
        return None

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def get_file_info(message: Message):
    """Extract file information from message"""
    if message.document:
        return {
            'name': message.document.file_name or 'Unknown',
            'type': 'Document',
            'size': message.document.file_size
        }
    elif message.photo:
        return {
            'name': f"Photo_{message.photo.file_id[:10]}",
            'type': 'Photo',
            'size': message.photo.file_size
        }
    elif message.video:
        return {
            'name': message.video.file_name or f"Video_{message.video.file_id[:10]}",
            'type': 'Video',
            'size': message.video.file_size
        }
    elif message.audio:
        return {
            'name': message.audio.file_name or f"Audio_{message.audio.file_id[:10]}",
            'type': 'Audio',
            'size': message.audio.file_size
        }
    elif message.voice:
        return {
            'name': f"Voice_{message.voice.file_id[:10]}",
            'type': 'Voice',
            'size': message.voice.file_size
        }
    elif message.video_note:
        return {
            'name': f"VideoNote_{message.video_note.file_id[:10]}",
            'type': 'Video Note',
            'size': message.video_note.file_size
        }
    elif message.sticker:
        return {
            'name': f"Sticker_{message.sticker.file_id[:10]}",
            'type': 'Sticker',
            'size': message.sticker.file_size
        }
    elif message.animation:
        return {
            'name': f"GIF_{message.animation.file_id[:10]}",
            'type': 'Animation',
            'size': message.animation.file_size
        }
    return None

def format_file_size(size_bytes):
    """Convert bytes to human readable format"""
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.1f} {size_names[i]}"

async def add_user(user_id, first_name, username):
    """Add user to database if not exists"""
    cursor.execute("INSERT OR IGNORE INTO users (user_id, first_name, username) VALUES (?, ?, ?)", 
                   (user_id, first_name, username))
    conn.commit()

def extract_links_from_text(text):
    """Extract all Telegram links from text"""
    pattern = r'https?://t\.me/[^\s]+'
    return re.findall(pattern, text)

def calculate_expiry_time(minutes):
    """Calculate expiry timestamp"""
    if minutes <= 0:
        return None
    return datetime.now() + timedelta(minutes=minutes)

# ------------------ AUTO DELETE BACKGROUND TASK ------------------

async def auto_delete_expired_files():
    """Background task to delete expired files with auto-reconnect"""
    retry_delay = 60
    max_retry_delay = 300
    
    while True:
        try:
            current_time = datetime.now()
            
            # Delete expired files
            cursor.execute("""SELECT id, chat_id, message_id FROM files 
                            WHERE expiry_time IS NOT NULL AND expiry_time <= ?""", 
                          (current_time,))
            expired_files = cursor.fetchall()
            
            for file_id, chat_id, msg_id in expired_files:
                try:
                    await bot.delete_messages(chat_id, msg_id)
                    cursor.execute("DELETE FROM files WHERE id = ?", (file_id,))
                    conn.commit()
                    logger.info(f"✅ Deleted expired file ID: {file_id}")
                except Exception as e:
                    logger.error(f"❌ Error deleting file {file_id}: {e}")
            
            # Delete expired batches
            cursor.execute("""SELECT id, start_msg, end_msg FROM batches 
                            WHERE expiry_time IS NOT NULL AND expiry_time <= ?""", 
                          (current_time,))
            expired_batches = cursor.fetchall()
            
            for batch_id, start_msg, end_msg in expired_batches:
                try:
                    # Delete all messages in batch
                    msg_ids = list(range(start_msg, end_msg + 1))
                    await bot.delete_messages(DB_CHANNEL, msg_ids)
                    cursor.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
                    conn.commit()
                    logger.info(f"✅ Deleted expired batch ID: {batch_id}")
                except Exception as e:
                    logger.error(f"❌ Error deleting batch {batch_id}: {e}")
            
            # Reset retry delay on success
            retry_delay = 60
            
        except (OSError, socket.error, TimeoutError) as e:
            logger.warning(f"⚠️ Network error in auto-delete: {e}")
            logger.info(f"🔄 Retrying in {retry_delay} seconds...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
            continue
            
        except Exception as e:
            logger.error(f"❌ Auto-delete error: {e}")
        
        await asyncio.sleep(60)  # Check every minute

# ------------------ USER INTERFACE ------------------

@bot.on_message(filters.command("start") & filters.private)
async def start_command(_, message):
    user = message.from_user
    await add_user(user.id, user.first_name, user.username)
    
    # Check if user is banned
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user.id,))
    result = cursor.fetchone()
    if result and result[0] == 1:
        return await message.reply_text("🚫 You are banned from using this bot.")
    
    if len(message.command) > 1:
        # Handle file/batch links
        payload = decode_payload(message.command[1])
        if not payload:
            return await message.reply_text("⚠️ Invalid or expired link.")
        
        if payload.startswith("file_"):
            return await send_file(message, int(payload.split("_")[1]))
        elif payload.startswith("batch_"):
            return await send_batch(message, int(payload.split("_")[1]))
    
    # Show appropriate menu based on user role
    if is_admin(user.id):
        return await show_admin_menu(message)
    else:
        return await show_user_menu(message)

async def show_user_menu(message):
    """Show menu for regular users"""
    buttons = [
        [InlineKeyboardButton("🔍 Search Files", callback_data="search_prompt"),
         InlineKeyboardButton("ℹ️ About", callback_data="about")],
        [InlineKeyboardButton("📖 How to Use", callback_data="help"),
         InlineKeyboardButton("📞 Contact Admin", callback_data="contact")]
    ]
    
    await message.reply_text(
        f"👋 **Welcome {message.from_user.first_name}!**\n\n"
        "👩 **Hi I am your assistant Sarah**\n\n"
        "🔹 Use /search to find files\n"
        "🔹 Click on file links to download\n"
        "🔹 All file types are supported",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def show_admin_menu(message):
    """Show menu for admin users"""
    # Get statistics
    cursor.execute("SELECT COUNT(*) FROM files")
    total_files = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM batches")
    total_batches = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM search_posts")
    total_posts = cursor.fetchone()[0]
    
    buttons = [
        [InlineKeyboardButton("📊 Statistics", callback_data="stats"),
         InlineKeyboardButton("📁 Batch Mode", callback_data="batch_help")],
        [InlineKeyboardButton("👥 User Management", callback_data="user_mgmt"),
         InlineKeyboardButton("📢 Broadcast", callback_data="broadcast_menu")],
        [InlineKeyboardButton("🔍 Search Posts", callback_data="search_posts_mgmt"),
         InlineKeyboardButton("🔧 Settings", callback_data="settings")],
        [InlineKeyboardButton("ℹ️ About", callback_data="about")]
    ]
    
    await message.reply_text(
        f"🛡️ **Admin Panel - Welcome {message.from_user.first_name}!**\n\n"
        f"📊 **Quick Stats:**\n"
        f"• Files: `{total_files}`\n"
        f"• Batches: `{total_batches}`\n"
        f"• Users: `{total_users}`\n"
        f"• Search Posts: `{total_posts}`\n\n"
        f"📤 **Upload Files:** Just send any media file\n"
        f"📦 **Create Batch:** Use batch mode\n"
        f"🔍 **Add Search Post:** Forward post with /addpost\n\n"
        f"Choose an option below:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@bot.on_callback_query()
async def handle_callbacks(_, query):
    data = query.data
    user_id = query.from_user.id
    
    if data == "help":
        await query.message.edit_text(
            "📖 **How to Use This Bot**\n\n"
            "🔸 **For Users:**\n"
            "• Use `/search <movie name>` to find files\n"
            "• Click on file links to download\n"
            "• All file types are supported\n"
            "• Links work until admin-set expiry\n\n"
            "🔸 **File Types Supported:**\n"
            "• Documents, Videos, Photos\n"
            "• Audio, Voice messages\n"
            "• Stickers, Animations\n"
            "• And much more!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
    
    elif data == "about":
        await query.message.edit_text(
            "🔹 **Version:** 3.0 Enhanced\n"
            "🔹 **Features:**\n"
            "• All media types support\n"
            "• Batch download\n"
            "• Smart search system\n"
            "• Auto-delete after expiry\n"
            "• User management\n"
            "• Broadcasting system\n"
            "🔹 **Storage:** Telegram Cloud\n"
            "🔹 **Developed by:** @x0deyen\n"
            "🔹 **Status:** Active ✅",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
    
    elif data == "contact":
        await query.message.edit_text(
            "📞 **Contact Information**\n\n"
            "Need help? Contact our admins:\n\n"
            f"👨‍💼 **Admin:** @x0deyen\n\n"
            "⏰ **Response Time:** Usually within 24 hours\n"
            "📝 **For:** Technical issues, file requests, queries",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
    
    elif data == "search_prompt":
        pending_actions[user_id] = "awaiting_search"
        await query.message.reply_text(
            "🔍 **Search Files**\n\n"
            "Please send me the movie/file name you want to search:\n\n"
            "Example: `Avengers` or `Python Tutorial`"
        )
    
    elif data == "stats" and is_admin(user_id):
        cursor.execute("SELECT COUNT(*) FROM files")
        total_files = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM batches")
        total_batches = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        banned_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT SUM(file_size) FROM files")
        total_size = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM search_posts")
        search_posts = cursor.fetchone()[0]
        
        await query.message.edit_text(
            "📊 **Detailed Statistics**\n\n"
            f"📁 **Files:** {total_files}\n"
            f"📦 **Batches:** {total_batches}\n"
            f"🔍 **Search Posts:** {search_posts}\n"
            f"👥 **Total Users:** {total_users}\n"
            f"🚫 **Banned Users:** {banned_users}\n"
            f"💾 **Storage Used:** {format_file_size(total_size)}\n\n"
            f"🤖 **Bot Status:** Online ✅\n"
            f"📈 **Performance:** Excellent",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
    
    elif data == "batch_help" and is_admin(user_id):
        await query.message.edit_text(
            "📦 **Batch Mode Guide**\n\n"
            "🔸 **How to create batches:**\n"
            "1. Use `/startbatch <name> <delete_minutes>` command\n"
            "   Example: `/startbatch MoviePack 1440` (deletes after 24 hours)\n"
            "2. Upload your files one by one\n"
            "3. Use `/endbatch` when done\n\n"
            "🔸 **Alternative method:**\n"
            "Use `/newbatch <start_id> <end_id> <delete_mins> [name]`\n\n"
            "🔸 **Delete time:**\n"
            "• 0 = Never delete\n"
            "• 60 = 1 hour\n"
            "• 1440 = 24 hours\n"
            "• 10080 = 7 days",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
    
    elif data == "user_mgmt" and is_admin(user_id):
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 0")
        active_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        banned_users = cursor.fetchone()[0]
        
        buttons = [
            [InlineKeyboardButton("👥 List Users", callback_data="list_users"),
             InlineKeyboardButton("🚫 Banned Users", callback_data="list_banned")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]
        
        await query.message.edit_text(
            "👥 **User Management**\n\n"
            f"✅ **Active Users:** {active_users}\n"
            f"🚫 **Banned Users:** {banned_users}\n\n"
            "**Commands:**\n"
            "• `/ban <user_id>` - Ban a user\n"
            "• `/unban <user_id>` - Unban a user\n"
            "• `/userinfo <user_id>` - Get user details",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    
    elif data == "list_users" and is_admin(user_id):
        cursor.execute("SELECT user_id, first_name, username, join_date FROM users WHERE is_banned = 0 ORDER BY join_date DESC LIMIT 20")
        users = cursor.fetchall()
        
        text = "👥 **Active Users (Last 20)**\n\n"
        for uid, fname, uname, jdate in users:
            uname_str = f"@{uname}" if uname else "No username"
            text += f"• {fname} ({uname_str})\n  ID: `{uid}`\n\n"
        
        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="user_mgmt")]])
        )
    
    elif data == "list_banned" and is_admin(user_id):
        cursor.execute("SELECT user_id, first_name, username FROM users WHERE is_banned = 1")
        users = cursor.fetchall()
        
        if not users:
            text = "✅ No banned users"
        else:
            text = "🚫 **Banned Users**\n\n"
            for uid, fname, uname in users:
                uname_str = f"@{uname}" if uname else "No username"
                text += f"• {fname} ({uname_str})\n  ID: `{uid}`\n\n"
        
        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="user_mgmt")]])
        )
    
    elif data == "broadcast_menu" and is_admin(user_id):
        pending_actions[user_id] = "awaiting_broadcast"
        await query.message.reply_text(
            "📢 **Broadcast Message**\n\n"
            "Send me the message you want to broadcast to all users.\n"
            "You can send text, photos, videos, or any media."
        )
    
    elif data == "settings" and is_admin(user_id):
        buttons = [
            [InlineKeyboardButton("🗑️ Clean Expired", callback_data="clean_expired"),
             InlineKeyboardButton("📊 DB Stats", callback_data="db_stats")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]
        
        await query.message.edit_text(
            "🔧 **Settings**\n\n"
            "**Storage Info:**\n"
            "• Files stored on Telegram Cloud\n"
            "• Bot stores only metadata locally\n"
            "• Auto-delete runs every minute\n\n"
            "**Commands:**\n"
            "• `/setdelete <file_id> <minutes>` - Set delete time\n"
            "• `/cleardb` - Clear database (keeps files)",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    
    elif data == "clean_expired" and is_admin(user_id):
        # Manually trigger cleanup
        current_time = datetime.now()
        cursor.execute("SELECT COUNT(*) FROM files WHERE expiry_time IS NOT NULL AND expiry_time <= ?", (current_time,))
        expired_files = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM batches WHERE expiry_time IS NOT NULL AND expiry_time <= ?", (current_time,))
        expired_batches = cursor.fetchone()[0]
        
        await query.answer(f"Found {expired_files} files and {expired_batches} batches to delete", show_alert=True)
    
    elif data == "db_stats" and is_admin(user_id):
        cursor.execute("SELECT COUNT(*) FROM files")
        total_files = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM files WHERE expiry_time IS NOT NULL")
        expiring_files = cursor.fetchone()[0]
        
        await query.message.edit_text(
            "📊 **Database Statistics**\n\n"
            f"📁 **Total Files:** {total_files}\n"
            f"⏰ **Expiring Files:** {expiring_files}\n"
            f"💾 **Storage:** Telegram Cloud\n"
            f"🗄️ **Local DB Size:** ~{format_file_size(conn.total_changes * 1024)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="settings")]])
        )
    
    elif data == "search_posts_mgmt" and is_admin(user_id):
        cursor.execute("SELECT COUNT(*) FROM search_posts")
        total_posts = cursor.fetchone()[0]
        
        buttons = [
            [InlineKeyboardButton("📝 List Posts", callback_data="list_search_posts")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]
        
        await query.message.edit_text(
            "🔍 **Search Posts Management**\n\n"
            f"📊 **Total Posts:** {total_posts}\n\n"
            "**How to add:**\n"
            "1. Forward any post to bot\n"
            "2. Reply to it with `/addpost <title> | <keywords>`\n\n"
            "**Example:**\n"
            "`/addpost Avengers Endgame | avengers, marvel, endgame`\n\n"
            "**Commands:**\n"
            "• `/deletepost <id>` - Delete a post\n"
            "• `/listposts` - List all posts",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    
    elif data == "list_search_posts" and is_admin(user_id):
        cursor.execute("SELECT id, title, keywords FROM search_posts ORDER BY id DESC LIMIT 15")
        posts = cursor.fetchall()
        
        if not posts:
            text = "❌ No search posts found"
        else:
            text = "🔍 **Search Posts (Last 15)**\n\n"
            for post_id, title, keywords in posts:
                text += f"#{post_id} • **{title}**\n"
                text += f"   Keywords: {keywords[:50]}...\n\n"
        
        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="search_posts_mgmt")]])
        )
    
    elif data == "back":
        if is_admin(user_id):
            await show_admin_menu(query.message)
        else:
            await show_user_menu(query.message)

# ------------------ ADMIN FILE UPLOAD ------------------

@bot.on_message(filters.private & ~filters.command(["start", "newbatch", "startbatch", "endbatch", "broadcast", 
                                                    "ban", "unban", "userinfo", "search", "addpost", "deletepost",
                                                    "listposts", "setdelete", "cleardb"]) & 
                (filters.document | filters.video | filters.audio | filters.photo | 
                 filters.voice | filters.video_note | filters.sticker | filters.animation))
async def handle_media_upload(_, message):
    user_id = message.from_user.id
    
    # Check if this is a broadcast message
    if user_id in pending_actions and pending_actions[user_id] == "awaiting_broadcast":
        return await handle_broadcast_message(_, message)
    
    if not is_admin(user_id):
        return await message.reply_text(
            "❌ **Access Denied**\n\n"
            "Only administrators can upload files.\n"
            "Use /search to find files."
        )
    
    # Get file information
    file_info = get_file_info(message)
    if not file_info:
        return await message.reply_text("❌ Unsupported file type.")
    
    # Ask for delete time
    pending_actions[user_id] = {"action": "awaiting_delete_time", "message": message, "file_info": file_info}
    
    buttons = [
        [InlineKeyboardButton("⏰ 10 min", callback_data="delete_10"),
         InlineKeyboardButton("⏰ 1 hour", callback_data="delete_60"),
         InlineKeyboardButton("⏰ 24 hours", callback_data="delete_1440")],
        [InlineKeyboardButton("⏰ 7 days", callback_data="delete_10080"),
         InlineKeyboardButton("♾️ Never", callback_data="delete_0")]
    ]
    
    await message.reply_text(
        f"📁 **File Received**\n\n"
        f"**Name:** {file_info['name']}\n"
        f"**Type:** {file_info['type']}\n"
        f"**Size:** {format_file_size(file_info['size'])}\n\n"
        f"⏰ **When should this file be deleted?**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@bot.on_callback_query(filters.regex(r"^delete_\d+$"))
async def handle_delete_time_selection(_, query):
    user_id = query.from_user.id
    
    if user_id not in pending_actions:
        return await query.answer("❌ Session expired", show_alert=True)
    
    action_data = pending_actions[user_id]
    if action_data.get("action") != "awaiting_delete_time":
        return await query.answer("❌ Invalid action", show_alert=True)
    
    delete_minutes = int(query.data.split("_")[1])
    original_message = action_data["message"]
    file_info = action_data["file_info"]
    
    # Forward to database channel
    try:
        sent = await original_message.forward(DB_CHANNEL)
    except Exception as e:
        return await query.message.edit_text(f"❌ Failed to save file: {str(e)}")
    
    # Calculate expiry time
    expiry_time = calculate_expiry_time(delete_minutes)
    
    # Save to database
    cursor.execute("""INSERT INTO files (chat_id, message_id, file_name, file_type, file_size, uploaded_by, delete_after_minutes, expiry_time) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", 
                  (DB_CHANNEL, sent.id, file_info['name'], file_info['type'], 
                   file_info['size'], user_id, delete_minutes, expiry_time))
    conn.commit()
    file_id = cursor.lastrowid
    
    # Generate link
    token = encode_payload(f"file_{file_id}")
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={token}"
    
    # Clear pending action
    del pending_actions[user_id]
    
    delete_info = "Never" if delete_minutes == 0 else f"{delete_minutes} minutes"
    
    buttons = [
        [InlineKeyboardButton("🚀 Open Link", url=link)],
        [InlineKeyboardButton("📊 View Stats", callback_data="stats")]
    ]
    
    await query.message.edit_text(
        f"✅ **File Uploaded Successfully!**\n\n"
        f"📝 **File Details:**\n"
        f"• Name: `{file_info['name']}`\n"
        f"• Type: `{file_info['type']}`\n"
        f"• Size: `{format_file_size(file_info['size'])}`\n"
        f"• Auto-delete: `{delete_info}`\n\n"
        f"🔗 **Share Link:**\n`{link}`\n\n"
        f"💡 Anyone with this link can download the file.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ------------------ BATCH SYSTEM ------------------

@bot.on_message(filters.private & filters.command("startbatch"))
async def start_batch_upload(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Only admins can create batches.")
    
    try:
        parts = message.command[1:]
        if len(parts) < 2:
            return await message.reply_text(
                "📦 **Start Batch Upload**\n\n"
                "**Usage:** `/startbatch <batch_name> <delete_minutes>`\n\n"
                "**Example:** `/startbatch MoviePack 1440`\n"
                "(Will delete after 24 hours)\n\n"
                "**Delete times:**\n"
                "• 0 = Never delete\n"
                "• 10 = 10 minutes\n"
                "• 60 = 1 hour\n"
                "• 1440 = 24 hours\n"
                "• 10080 = 7 days"
            )
        
        delete_minutes = int(parts[-1])
        batch_name = " ".join(parts[:-1])
        
    except ValueError:
        return await message.reply_text("❌ Last parameter must be a number (delete time in minutes).")
    except:
        return await message.reply_text("❌ Invalid format. Use: `/startbatch <name> <delete_minutes>`")
    
    session_id = f"{message.from_user.id}_{int(datetime.now().timestamp())}"
    
    # Get current message ID from DB channel
    try:
        test_msg = await bot.send_message(DB_CHANNEL, f"📦 **Batch Start:** {batch_name}")
        start_msg_id = test_msg.id
        await test_msg.delete()
    except:
        return await message.reply_text("❌ Failed to access database channel.")
    
    cursor.execute("""INSERT INTO batch_upload_sessions (session_id, admin_id, batch_name, start_msg_id, delete_after_minutes) 
                     VALUES (?, ?, ?, ?, ?)""", (session_id, message.from_user.id, batch_name, start_msg_id, delete_minutes))
    conn.commit()
    
    delete_info = "Never" if delete_minutes == 0 else f"{delete_minutes} minutes"
    
    await message.reply_text(
        f"📦 **Batch Upload Started**\n\n"
        f"📝 **Batch Name:** {batch_name}\n"
        f"⏰ **Auto-delete:** {delete_info}\n\n"
        f"📤 Now send me the files you want to include in this batch.\n"
        f"When you're done, use `/endbatch` command.\n\n"
        f"🔄 **Session ID:** `{session_id}`"
    )

@bot.on_message(filters.private & filters.command("endbatch"))
async def end_batch_upload(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Only admins can end batches.")
    
    # Find active session for this admin
    cursor.execute("SELECT * FROM batch_upload_sessions WHERE admin_id = ? AND status = 'waiting_end'", 
                   (message.from_user.id,))
    session = cursor.fetchone()
    
    if not session:
        return await message.reply_text("❌ No active batch session found.")
    
    session_id, admin_id, batch_name, start_msg_id, status, delete_minutes = session
    
    # Get current message ID from DB channel
    try:
        test_msg = await bot.send_message(DB_CHANNEL, f"📦 **Batch End:** {batch_name}")
        end_msg_id = test_msg.id - 1
        await test_msg.delete()
    except:
        return await message.reply_text("❌ Failed to access database channel.")
    
    # Calculate expiry time
    expiry_time = calculate_expiry_time(delete_minutes)
    
    # Create batch
    cursor.execute("INSERT INTO batches (batch_name, start_msg, end_msg, created_by, delete_after_minutes, expiry_time) VALUES (?, ?, ?, ?, ?, ?)", 
                   (batch_name, start_msg_id, end_msg_id, admin_id, delete_minutes, expiry_time))
    conn.commit()
    batch_id = cursor.lastrowid
    
    # Clean up session
    cursor.execute("DELETE FROM batch_upload_sessions WHERE session_id = ?", (session_id,))
    conn.commit()
    
    # Generate link
    token = encode_payload(f"batch_{batch_id}")
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={token}"
    
    file_count = end_msg_id - start_msg_id + 1
    delete_info = "Never" if delete_minutes == 0 else f"{delete_minutes} minutes"
    
    buttons = [
        [InlineKeyboardButton("🚀 Open Batch Link", url=link)],
        [InlineKeyboardButton("📊 View Stats", callback_data="stats")]
    ]
    
    await message.reply_text(
        f"✅ **Batch Created Successfully!**\n\n"
        f"📦 **Batch Name:** {batch_name}\n"
        f"📁 **Files:** {file_count} files\n"
        f"🆔 **Batch ID:** #{batch_id}\n"
        f"⏰ **Auto-delete:** {delete_info}\n\n"
        f"🔗 **Share Link:**\n`{link}`\n\n"
        f"💡 Anyone with this link can download all files in the batch.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@bot.on_message(filters.private & filters.command("newbatch"))
async def new_batch_traditional(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Only admins can create batches.")
    
    try:
        parts = message.text.split()
        if len(parts) < 4:
            return await message.reply_text(
                "📦 **Create Batch**\n\n"
                "**Usage:** `/newbatch <start_id> <end_id> <delete_mins> [name]`\n\n"
                "**Example:** `/newbatch 100 150 1440 My Movies`\n"
                "(Deletes after 24 hours)"
            )
        
        start_id = int(parts[1])
        end_id = int(parts[2])
        delete_minutes = int(parts[3])
        batch_name = " ".join(parts[4:]) if len(parts) > 4 else f"Batch {start_id}-{end_id}"
        
        if start_id >= end_id:
            return await message.reply_text("❌ Start message ID must be less than end message ID.")
        
    except ValueError:
        return await message.reply_text("❌ Invalid format. IDs and delete time must be numbers.")
    
    # Calculate expiry time
    expiry_time = calculate_expiry_time(delete_minutes)
    
    # Create batch
    cursor.execute("INSERT INTO batches (batch_name, start_msg, end_msg, created_by, delete_after_minutes, expiry_time) VALUES (?, ?, ?, ?, ?, ?)", 
                   (batch_name, start_id, end_id, message.from_user.id, delete_minutes, expiry_time))
    conn.commit()
    batch_id = cursor.lastrowid
    
    # Generate link
    token = encode_payload(f"batch_{batch_id}")
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={token}"
    
    file_count = end_id - start_id + 1
    delete_info = "Never" if delete_minutes == 0 else f"{delete_minutes} minutes"
    
    buttons = [
        [InlineKeyboardButton("🚀 Open Batch Link", url=link)],
        [InlineKeyboardButton("📊 View Stats", callback_data="stats")]
    ]
    
    await message.reply_text(
        f"✅ **Batch Created Successfully!**\n\n"
        f"📦 **Batch Name:** {batch_name}\n"
        f"📁 **Files:** {file_count} files\n"
        f"🆔 **Batch ID:** #{batch_id}\n"
        f"⏰ **Auto-delete:** {delete_info}\n\n"
        f"🔗 **Share Link:**\n`{link}`\n\n"
        f"💡 Anyone with this link can download all files in the batch.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ------------------ FILE DELIVERY ------------------

async def send_file(message, file_id):
    """Send a single file to user with error handling"""
    cursor.execute("SELECT chat_id, message_id, file_name, file_type, expiry_time FROM files WHERE id=?", (file_id,))
    row = cursor.fetchone()
    
    if not row:
        return await message.reply_text(
            "❌ **File Not Found**\n\n"
            "The requested file could not be found. It may have been deleted or the link is invalid."
        )
    
    chat_id, msg_id, file_name, file_type, expiry_time = row
    
    # Check if expired
    if expiry_time and datetime.fromisoformat(expiry_time) <= datetime.now():
        cursor.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()
        return await message.reply_text(
            "❌ **File Expired**\n\n"
            "This file has expired and is no longer available."
        )
    
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            await message.reply_text(
                f"📁 **Downloading File...**\n\n"
                f"📝 **Name:** {file_name}\n"
                f"📂 **Type:** {file_type}\n\n"
                f"⏳ Please wait while we fetch your file..."
            )
            
            await bot.copy_message(message.chat.id, chat_id, msg_id)
            
            expiry_msg = ""
            if expiry_time:
                expiry_dt = datetime.fromisoformat(expiry_time)
                time_left = expiry_dt - datetime.now()
                hours_left = int(time_left.total_seconds() / 3600)
                expiry_msg = f"\n⏰ File expires in: {hours_left} hours"
            
            await message.reply_text(
                f"✅ **Download Complete!**\n\n"
                f"File: {file_name}\n"
                f"⚠️ Download it or forward to Saved Messages!{expiry_msg}"
            )
            break
            
        except FloodWait as e:
            logger.warning(f"FloodWait: Sleeping for {e.value} seconds")
            await asyncio.sleep(e.value)
            retry_count += 1
            
        except (OSError, socket.error, TimeoutError) as e:
            logger.warning(f"Network error: {e}. Retrying...")
            await asyncio.sleep(5)
            retry_count += 1
            
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            await message.reply_text(
                f"❌ **Download Failed**\n\n"
                f"Error: {str(e)}\n"
                f"Please try again later or contact admin."
            )
            break

async def send_batch(message, batch_id):
    """Send all files in a batch to user with error handling"""
    cursor.execute("SELECT batch_name, start_msg, end_msg, expiry_time FROM batches WHERE id=?", (batch_id,))
    row = cursor.fetchone()
    
    if not row:
        return await message.reply_text(
            "❌ **Batch Not Found**\n\n"
            "The requested batch could not be found. It may have been deleted or the link is invalid."
        )
    
    batch_name, start, end, expiry_time = row
    
    # Check if expired
    if expiry_time and datetime.fromisoformat(expiry_time) <= datetime.now():
        cursor.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
        conn.commit()
        return await message.reply_text(
            "❌ **Batch Expired**\n\n"
            "This batch has expired and is no longer available."
        )
    
    file_count = end - start + 1
    
    expiry_msg = ""
    if expiry_time:
        expiry_dt = datetime.fromisoformat(expiry_time)
        time_left = expiry_dt - datetime.now()
        hours_left = int(time_left.total_seconds() / 3600)
        expiry_msg = f"\n⏰ Batch expires in: {hours_left} hours"
    
    await message.reply_text(
        f"📦 **Batch Download Started**\n\n"
        f"📝 **Name:** {batch_name}\n"
        f"📁 **Files:** {file_count} files{expiry_msg}\n\n"
        f"⏳ Downloading..."
    )
    
    successful = 0
    failed = 0
    
    for msg_id in range(start, end + 1):
        max_retries = 2
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                await bot.copy_message(message.chat.id, DB_CHANNEL, msg_id)
                successful += 1
                await asyncio.sleep(0.5)
                break
                
            except FloodWait as e:
                logger.warning(f"FloodWait: Sleeping for {e.value} seconds")
                await asyncio.sleep(e.value)
                retry_count += 1
                
            except (OSError, socket.error, TimeoutError):
                logger.warning("Network error, retrying...")
                await asyncio.sleep(3)
                retry_count += 1
                
            except:
                failed += 1
                break
    
    await message.reply_text(
        f"✅ **Batch Download Complete!**\n\n"
        f"📦 **{batch_name}**\n"
        f"✅ **Downloaded:** {successful} files\n"
        f"❌ **Failed:** {failed} files\n\n"
        f"💡 Save them before they expire!"
    )

# ------------------ SEARCH SYSTEM ------------------

@bot.on_message(filters.private & filters.command("search"))
async def search_files(_, message):
    user = message.from_user
    await add_user(user.id, user.first_name, user.username)
    
    # Check if user is banned
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user.id,))
    result = cursor.fetchone()
    if result and result[0] == 1:
        return await message.reply_text("🚫 You are banned from using this bot.")
    
    if len(message.command) < 2:
        return await message.reply_text(
            "🔍 **Search Files**\n\n"
            "**Usage:** `/search <movie/file name>`\n\n"
            "**Example:** `/search Avengers`"
        )
    
    query = " ".join(message.command[1:]).lower()
    
    # Search in database
    cursor.execute("""SELECT id, title, post_chat_id, post_message_id 
                     FROM search_posts 
                     WHERE LOWER(title) LIKE ? OR LOWER(keywords) LIKE ?""", 
                   (f"%{query}%", f"%{query}%"))
    
    results = cursor.fetchall()
    
    if not results:
        return await message.reply_text(
            f"❌ **No Results Found**\n\n"
            f"Search query: `{query}`\n\n"
            f"Try different keywords or contact admin to add this file."
        )
    
    if len(results) == 1:
        # Send the post directly
        post_id, title, chat_id, msg_id = results[0]
        try:
            await message.reply_text(f"🔍 Found: **{title}**\n\nSending...")
            await bot.copy_message(message.chat.id, chat_id, msg_id)
        except Exception as e:
            await message.reply_text(f"❌ Error fetching post: {str(e)}")
    else:
        # Show multiple results
        text = f"🔍 **Search Results for:** `{query}`\n\n"
        text += f"Found {len(results)} results:\n\n"
        
        buttons = []
        for post_id, title, _, _ in results[:10]:  # Limit to 10
            buttons.append([InlineKeyboardButton(
                f"📁 {title}", 
                callback_data=f"getpost_{post_id}"
            )])
        
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_callback_query(filters.regex(r"^getpost_\d+$"))
async def send_search_post(_, query):
    post_id = int(query.data.split("_")[1])
    
    cursor.execute("SELECT title, post_chat_id, post_message_id FROM search_posts WHERE id = ?", (post_id,))
    result = cursor.fetchone()
    
    if not result:
        return await query.answer("❌ Post not found", show_alert=True)
    
    title, chat_id, msg_id = result
    
    try:
        await query.answer(f"Sending {title}...", show_alert=False)
        await bot.copy_message(query.message.chat.id, chat_id, msg_id)
    except Exception as e:
        await query.answer(f"❌ Error: {str(e)}", show_alert=True)

@bot.on_message(filters.private & filters.command("addpost") & filters.reply)
async def add_search_post(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Only admins can add search posts.")
    
    if len(message.command) < 2:
        return await message.reply_text(
            "🔍 **Add Search Post**\n\n"
            "**Usage:** Reply to a post with:\n"
            "`/addpost <title> | <keywords>`\n\n"
            "**Example:**\n"
            "`/addpost Avengers Endgame | avengers, marvel, endgame, infinity war`"
        )
    
    text = " ".join(message.command[1:])
    
    if "|" not in text:
        return await message.reply_text(
            "❌ Invalid format.\n\n"
            "Use: `/addpost <title> | <keywords>`"
        )
    
    parts = text.split("|")
    title = parts[0].strip()
    keywords = parts[1].strip() if len(parts) > 1 else ""
    
    replied_msg = message.reply_to_message
    
    # Forward post to DB channel to preserve it
    try:
        forwarded = await replied_msg.forward(DB_CHANNEL)
        
        cursor.execute("""INSERT INTO search_posts (title, keywords, post_chat_id, post_message_id, added_by) 
                         VALUES (?, ?, ?, ?, ?)""", 
                      (title, keywords, DB_CHANNEL, forwarded.id, message.from_user.id))
        conn.commit()
        post_id = cursor.lastrowid
        
        await message.reply_text(
            f"✅ **Search Post Added!**\n\n"
            f"🆔 **Post ID:** #{post_id}\n"
            f"📝 **Title:** {title}\n"
            f"🔑 **Keywords:** {keywords}\n\n"
            f"Users can now search for this using `/search`"
        )
        
    except Exception as e:
        await message.reply_text(f"❌ Error adding post: {str(e)}")

@bot.on_message(filters.private & filters.command("deletepost"))
async def delete_search_post(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Only admins can delete posts.")
    
    if len(message.command) < 2:
        return await message.reply_text(
            "🗑️ **Delete Search Post**\n\n"
            "**Usage:** `/deletepost <post_id>`\n\n"
            "**Example:** `/deletepost 5`"
        )
    
    try:
        post_id = int(message.command[1])
        
        cursor.execute("SELECT title FROM search_posts WHERE id = ?", (post_id,))
        result = cursor.fetchone()
        
        if not result:
            return await message.reply_text("❌ Post not found.")
        
        title = result[0]
        
        cursor.execute("DELETE FROM search_posts WHERE id = ?", (post_id,))
        conn.commit()
        
        await message.reply_text(
            f"✅ **Post Deleted**\n\n"
            f"Post ID #{post_id} ({title}) has been removed."
        )
        
    except ValueError:
        await message.reply_text("❌ Post ID must be a number.")
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@bot.on_message(filters.private & filters.command("listposts"))
async def list_posts_command(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Only admins can view this.")
    
    cursor.execute("SELECT id, title, keywords FROM search_posts ORDER BY id DESC LIMIT 20")
    posts = cursor.fetchall()
    
    if not posts:
        return await message.reply_text("❌ No search posts found.")
    
    text = "🔍 **Search Posts (Last 20)**\n\n"
    for post_id, title, keywords in posts:
        text += f"#{post_id} • **{title}**\n"
        text += f"   Keywords: {keywords[:40]}...\n\n"
    
    await message.reply_text(text)

# ------------------ USER MANAGEMENT ------------------

@bot.on_message(filters.private & filters.command("ban"))
async def ban_user(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Only admins can ban users.")
    
    if len(message.command) < 2:
        return await message.reply_text(
            "🚫 **Ban User**\n\n"
            "**Usage:** `/ban <user_id>`\n\n"
            "**Example:** `/ban 123456789`"
        )
    
    try:
        user_id = int(message.command[1])
        
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        
        if cursor.rowcount > 0:
            await message.reply_text(f"✅ User {user_id} has been banned.")
        else:
            await message.reply_text(f"❌ User {user_id} not found in database.")
            
    except ValueError:
        await message.reply_text("❌ User ID must be a number.")

@bot.on_message(filters.private & filters.command("unban"))
async def unban_user(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Only admins can unban users.")
    
    if len(message.command) < 2:
        return await message.reply_text(
            "✅ **Unban User**\n\n"
            "**Usage:** `/unban <user_id>`\n\n"
            "**Example:** `/unban 123456789`"
        )
    
    try:
        user_id = int(message.command[1])
        
        cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        
        if cursor.rowcount > 0:
            await message.reply_text(f"✅ User {user_id} has been unbanned.")
        else:
            await message.reply_text(f"❌ User {user_id} not found in database.")
            
    except ValueError:
        await message.reply_text("❌ User ID must be a number.")

@bot.on_message(filters.private & filters.command("userinfo"))
async def user_info(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Only admins can view user info.")
    
    if len(message.command) < 2:
        return await message.reply_text(
            "ℹ️ **User Info**\n\n"
            "**Usage:** `/userinfo <user_id>`\n\n"
            "**Example:** `/userinfo 123456789`"
        )
    
    try:
        user_id = int(message.command[1])
        
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if not result:
            return await message.reply_text(f"❌ User {user_id} not found.")
        
        uid, fname, uname, jdate, is_banned = result
        status = "🚫 Banned" if is_banned else "✅ Active"
        uname_str = f"@{uname}" if uname else "No username"
        
        await message.reply_text(
            f"👤 **User Information**\n\n"
            f"**ID:** `{uid}`\n"
            f"**Name:** {fname}\n"
            f"**Username:** {uname_str}\n"
            f"**Joined:** {jdate}\n"
            f"**Status:** {status}"
        )
        
    except ValueError:
        await message.reply_text("❌ User ID must be a number.")

# ------------------ BROADCAST SYSTEM ------------------

async def handle_broadcast_message(_, message):
    """Handle the actual broadcast message from admin"""
    user_id = message.from_user.id
    
    if user_id not in pending_actions or pending_actions[user_id] != "awaiting_broadcast":
        return
    
    # Get all active users
    cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
    users = cursor.fetchall()
    
    if not users:
        del pending_actions[user_id]
        return await message.reply_text("❌ No users to broadcast to.")
    
    # Confirm
    buttons = [
        [InlineKeyboardButton("✅ Send to All", callback_data=f"confirm_bcast")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_bcast")]
    ]
    
    # Store broadcast data
    cursor.execute("INSERT OR REPLACE INTO broadcast_state (admin_id, message, total_users) VALUES (?, ?, ?)",
                  (user_id, message.text or "Media message", len(users)))
    conn.commit()
    
    pending_actions[user_id] = {"action": "broadcast_confirm", "message": message, "users": users}
    
    await message.reply_text(
        f"📢 **Confirm Broadcast**\n\n"
        f"Recipients: {len(users)} users\n\n"
        f"Are you sure?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@bot.on_callback_query(filters.regex(r"^confirm_bcast$"))
async def confirm_broadcast(_, query):
    user_id = query.from_user.id
    
    if user_id not in pending_actions:
        return await query.answer("❌ Session expired", show_alert=True)
    
    data = pending_actions[user_id]
    if data.get("action") != "broadcast_confirm":
        return await query.answer("❌ Invalid action", show_alert=True)
    
    broadcast_msg = data["message"]
    users = data["users"]
    
    await query.message.edit_text("📢 Broadcasting... Please wait.")
    
    success = 0
    failed = 0
    
    for (uid,) in users:
        try:
            await broadcast_msg.copy(uid)
            success += 1
            await asyncio.sleep(0.05)  # Small delay to avoid flood
        except:
            failed += 1
    
    del pending_actions[user_id]
    cursor.execute("DELETE FROM broadcast_state WHERE admin_id = ?", (user_id,))
    conn.commit()
    
    await query.message.edit_text(
        f"✅ **Broadcast Complete!**\n\n"
        f"✅ Sent: {success}\n"
        f"❌ Failed: {failed}\n"
        f"📊 Total: {len(users)}"
    )

@bot.on_callback_query(filters.regex(r"^cancel_bcast$"))
async def cancel_broadcast(_, query):
    user_id = query.from_user.id
    
    if user_id in pending_actions:
        del pending_actions[user_id]
    
    cursor.execute("DELETE FROM broadcast_state WHERE admin_id = ?", (user_id,))
    conn.commit()
    
    await query.message.edit_text("❌ Broadcast cancelled.")

# ------------------ TEXT SEARCH HANDLER ------------------

@bot.on_message(filters.private & filters.text & ~filters.command(None))
async def handle_text_search(_, message):
    user_id = message.from_user.id
    
    # Check if expecting search
    if user_id in pending_actions and pending_actions[user_id] == "awaiting_search":
        del pending_actions[user_id]
        
        query = message.text.lower()
        
        cursor.execute("""SELECT id, title, post_chat_id, post_message_id 
                         FROM search_posts 
                         WHERE LOWER(title) LIKE ? OR LOWER(keywords) LIKE ?""", 
                       (f"%{query}%", f"%{query}%"))
        
        results = cursor.fetchall()
        
        if not results:
            return await message.reply_text(
                f"❌ **No Results Found**\n\n"
                f"Search query: `{query}`\n\n"
                f"Try different keywords."
            )
        
        if len(results) == 1:
            post_id, title, chat_id, msg_id = results[0]
            try:
                await message.reply_text(f"🔍 Found: **{title}**")
                await bot.copy_message(message.chat.id, chat_id, msg_id)
            except Exception as e:
                await message.reply_text(f"❌ Error: {str(e)}")
        else:
            text = f"🔍 **Search Results for:** `{query}`\n\n"
            text += f"Found {len(results)} results:\n\n"
            
            buttons = []
            for post_id, title, _, _ in results[:10]:
                buttons.append([InlineKeyboardButton(
                    f"📁 {title}", 
                    callback_data=f"getpost_{post_id}"
                )])
            
            await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

# ------------------ STARTUP ------------------

async def startup():
    """Initialize bot and start background tasks"""
    logger.info("🤖 Starting Enhanced File Store Bot...")
    logger.info("✅ Database initialized")
    logger.info("✅ Tables created/verified")
    
    # Start auto-delete background task
    asyncio.create_task(auto_delete_expired_files())
    logger.info("✅ Auto-delete task started")
    
    # Get bot info
    me = await bot.get_me()
    logger.info(f"✅ Bot @{me.username} is now running!")
    logger.info(f"📊 Ready to serve users")
    logger.info("\n" + "="*50)
    logger.info("FEATURES ENABLED:")
    logger.info("✅ File upload with auto-delete")
    logger.info("✅ Batch creation with expiry")
    logger.info("✅ Smart search system")
    logger.info("✅ User management & banning")
    logger.info("✅ Broadcasting to all users")
    logger.info("✅ Dashboard with statistics")
    logger.info("✅ Auto-reconnect on network loss")
    logger.info("="*50 + "\n")

async def run_bot_with_reconnect():
    """Run bot with automatic reconnection on network failure"""
    retry_delay = 5
    max_retry_delay = 300
    
    while True:
        try:
            logger.info("🚀 Starting bot...")
            await bot.start()
            await startup()
            
            # Keep the bot running
            await asyncio.Event().wait()
            
        except (OSError, socket.error, TimeoutError, RPCError) as e:
            logger.error(f"❌ Connection lost: {e}")
            logger.info(f"🔄 Attempting to reconnect in {retry_delay} seconds...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
            
            # Try to stop gracefully before reconnecting
            try:
                await bot.stop()
            except:
                pass
                
        except KeyboardInterrupt:
            logger.info("🛑 Stopping bot...")
            await bot.stop()
            break
            
        except Exception as e:
            logger.error(f"❌ Unexpected error: {e}")
            logger.info(f"🔄 Restarting in {retry_delay} seconds...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
            
            try:
                await bot.stop()
            except:
                pass

# Start the bot
if __name__ == "__main__":
    try:
        asyncio.run(run_bot_with_reconnect())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")