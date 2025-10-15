import sqlite3, base64
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from config import API_ID, API_HASH, BOT_TOKEN, DB_CHANNEL, ADMINS
import asyncio
from datetime import datetime

# Bot setup
bot = Client("FileStoreBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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
    upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_name TEXT,
    start_msg INTEGER,
    end_msg INTEGER,
    created_by INTEGER,
    created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    status TEXT DEFAULT 'waiting_end'
)""")

conn.commit()

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
        [InlineKeyboardButton("📖 How to Use", callback_data="help"),
         InlineKeyboardButton("ℹ️ About", callback_data="about")],
        [InlineKeyboardButton("📞 Contact Admin", callback_data="contact")]
    ]
    
    await message.reply_text(
        f"👋 **Welcome {message.from_user.first_name}!**\n\n"
        "👩 ** Hi i am your assistant Sarah**\n\n"
        "This bot helps you access & downlod files. ",
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
    
    buttons = [
        [InlineKeyboardButton("📊 Statistics", callback_data="stats"),
         InlineKeyboardButton("📁 Batch Mode", callback_data="batch_help")],
        [InlineKeyboardButton("👥 User Management", callback_data="user_mgmt"),
         InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")],
        [InlineKeyboardButton("ℹ️ About", callback_data="about"),
         InlineKeyboardButton("🔧 Settings", callback_data="settings")]
    ]
    
    await message.reply_text(
        f"🛡️ **Admin Panel - Welcome {message.from_user.first_name}!**\n\n"
        f"📊 **Quick Stats:**\n"
        f"• Files: `{total_files}`\n"
        f"• Batches: `{total_batches}`\n"
        f"• Users: `{total_users}`\n\n"
        f"📤 **Upload Files:** Just send any media file\n"
        f"📦 **Create Batch:** Use batch mode for multiple files\n\n"
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
            "• Click on file links to download\n"
            "• All file types are supported\n"
            "• Links work permanently\n\n"
            "🔸 **File Types Supported:**\n"
            "• Documents (PDF, DOC, etc.)\n"
            "• Videos (MP4, MKV, etc.)\n"
            "• Photos (JPG, PNG, etc.)\n"
            "• Audio files (MP3, etc.)\n"
            "• Voice messages\n"
            "• Stickers & Animations\n"
            "• And much more!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
    
    elif data == "about":
        await query.message.edit_text(
            "🔹 **Version:** 2.0\n"
            "🔹 **Features:**\n"
            "• All media types support\n"
            "• Batch download\n"
            "🔹 **Developed by : @x0deyen\n"
            "🔹 **Status:** Active ✅",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
    
    elif data == "contact":
        admin_list = ", ".join([f"@admin{i}" for i in range(1, len(ADMINS)+1)])
        await query.message.edit_text(
            "📞 **Contact Information**\n\n"
            "Need help? Contact our admins:\n\n"
            f"👨‍💼 **Admins:** {admin_list}\n\n"
            "⏰ **Response Time:** Usually within 24 hours\n"
            "📝 **For:** Technical issues, file requests, general queries",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
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
        
        await query.message.edit_text(
            "📊 **Detailed Statistics**\n\n"
            f"📁 **Files:** {total_files}\n"
            f"📦 **Batches:** {total_batches}\n"
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
            "1. Use `/startbatch <batch_name>` command\n"
            "2. Upload your files one by one\n"
            "3. Use `/endbatch` when done\n\n"
            "🔸 **Alternative method:**\n"
            "Use `/newbatch <start_id> <end_id>` with message IDs from DB channel\n\n"
            "🔸 **Benefits:**\n"
            "• Single link for multiple files\n"
            "• Organized file sharing\n"
            "• Easy management",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
    
    elif data == "back":
        if is_admin(user_id):
            await show_admin_menu(query.message)
        else:
            await show_user_menu(query.message)

# ------------------ ADMIN FILE UPLOAD ------------------

@bot.on_message(filters.private & ~filters.command(["start", "newbatch", "startbatch", "endbatch", "broadcast"]) & 
                (filters.document | filters.video | filters.audio | filters.photo | 
                 filters.voice | filters.video_note | filters.sticker | filters.animation))
async def handle_media_upload(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text(
            "❌ **Access Denied**\n\n"
            "Only administrators can upload files to this bot.\n"
            "If you need to upload files, please contact an admin."
        )
    
    # Get file information
    file_info = get_file_info(message)
    if not file_info:
        return await message.reply_text("❌ Unsupported file type.")
    
    # Forward to database channel
    try:
        sent = await message.forward(DB_CHANNEL)
    except Exception as e:
        return await message.reply_text(f"❌ Failed to save file: {str(e)}")
    
    # Save to database
    cursor.execute("""INSERT INTO files (chat_id, message_id, file_name, file_type, file_size, uploaded_by) 
                     VALUES (?, ?, ?, ?, ?, ?)""", 
                  (DB_CHANNEL, sent.id, file_info['name'], file_info['type'], 
                   file_info['size'], message.from_user.id))
    conn.commit()
    file_id = cursor.lastrowid
    
    # Generate link
    token = encode_payload(f"file_{file_id}")
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={token}"
    
    # Create response with file details
    buttons = [
        [InlineKeyboardButton("🚀Open link", url=link)],
        [InlineKeyboardButton("📊 View Stats", callback_data="stats")]
    ]
    
    await message.reply_text(
        f"✅ **File Uploaded Successfully!**\n\n"
        f"📝 **File Details:**\n"
        f"• Name: `{file_info['name']}`\n"
        f"• Type: `{file_info['type']}`\n"
        f"• Size: `{format_file_size(file_info['size'])}`\n\n"
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
        batch_name = " ".join(message.command[1:])
        if not batch_name:
            return await message.reply_text("Usage: `/startbatch <batch_name>`")
    except:
        return await message.reply_text("Usage: `/startbatch <batch_name>`")
    
    session_id = f"{message.from_user.id}_{int(datetime.now().timestamp())}"
    
    # Get current message ID from DB channel (next message will be start)
    try:
        test_msg = await bot.send_message(DB_CHANNEL, f"📦 **Batch Start:** {batch_name}")
        start_msg_id = test_msg.id
        await test_msg.delete()
    except:
        return await message.reply_text("❌ Failed to access database channel.")
    
    cursor.execute("""INSERT INTO batch_upload_sessions (session_id, admin_id, batch_name, start_msg_id) 
                     VALUES (?, ?, ?, ?)""", (session_id, message.from_user.id, batch_name, start_msg_id))
    conn.commit()
    
    await message.reply_text(
        f"📦 **Batch Upload Started**\n\n"
        f"📝 **Batch Name:** {batch_name}\n\n"
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
    
    session_id, admin_id, batch_name, start_msg_id, status = session
    
    # Get current message ID from DB channel
    try:
        test_msg = await bot.send_message(DB_CHANNEL, f"📦 **Batch End:** {batch_name}")
        end_msg_id = test_msg.id - 1  # Previous message was the last file
        await test_msg.delete()
    except:
        return await message.reply_text("❌ Failed to access database channel.")
    
    # Create batch
    cursor.execute("INSERT INTO batches (batch_name, start_msg, end_msg, created_by) VALUES (?, ?, ?, ?)", 
                   (batch_name, start_msg_id, end_msg_id, admin_id))
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
    
    buttons = [
        [InlineKeyboardButton("🚀 Open Batch Link", url=link)],
        [InlineKeyboardButton("📊 View Stats", callback_data="stats")]
    ]
    
    await message.reply_text(
        f"✅ **Batch Created Successfully!**\n\n"
        f"📦 **Batch Name:** {batch_name}\n"
        f"📁 **Files:** {file_count} files\n"
        f"🆔 **Batch ID:** #{batch_id}\n\n"
        f"🔗 **Share Link:**\n`{link}`\n\n"
        f"💡 Anyone with this link can download all files in the batch.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# Traditional batch creation (existing method)
@bot.on_message(filters.private & filters.command("newbatch"))
async def new_batch_traditional(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Only admins can create batches.")
    
    try:
        parts = message.text.split()
        if len(parts) < 3:
            return await message.reply_text(
                "📦 **Create Batch**\n\n"
                "**Usage:** `/newbatch <start_msg_id> <end_msg_id> [batch_name]`\n\n"
                "**Example:** `/newbatch 100 150 My Movie Collection`"
            )
        
        start_id = int(parts[1])
        end_id = int(parts[2])
        batch_name = " ".join(parts[3:]) if len(parts) > 3 else f"Batch {start_id}-{end_id}"
        
        if start_id >= end_id:
            return await message.reply_text("❌ Start message ID must be less than end message ID.")
        
    except ValueError:
        return await message.reply_text("❌ Please provide valid message IDs (numbers only).")
    
    # Create batch
    cursor.execute("INSERT INTO batches (batch_name, start_msg, end_msg, created_by) VALUES (?, ?, ?, ?)", 
                   (batch_name, start_id, end_id, message.from_user.id))
    conn.commit()
    batch_id = cursor.lastrowid
    
    # Generate link
    token = encode_payload(f"batch_{batch_id}")
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={token}"
    
    file_count = end_id - start_id + 1
    
    buttons = [
        [InlineKeyboardButton("🚀 Open Batch Link", url=link)],
        [InlineKeyboardButton("📊 View Stats", callback_data="stats")]
    ]
    
    await message.reply_text(
        f"✅ **Batch Created Successfully!**\n\n"
        f"📦 **Batch Name:** {batch_name}\n"
        f"📁 **Files:** {file_count} files\n"
        f"🆔 **Batch ID:** #{batch_id}\n\n"
        f"🔗 **Share Link:**\n`{link}`\n\n"
        f"💡 Anyone with this link can download all files in the batch.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ------------------ FILE DELIVERY ------------------

async def send_file(message, file_id):
    """Send a single file to user"""
    cursor.execute("SELECT chat_id, message_id, file_name, file_type FROM files WHERE id=?", (file_id,))
    row = cursor.fetchone()
    
    if not row:
        return await message.reply_text(
            "❌ **File Not Found**\n\n"
            "The requested file could not be found. It may have been deleted or the link is invalid."
        )
    
    chat_id, msg_id, file_name, file_type = row
    
    try:
        # Send file info first
        await message.reply_text(
            f"📁 **Downloading File...**\n\n"
            f"📝 **Name:** {file_name}\n"
            f"📂 **Type:** {file_type}\n\n"
            f"⏳ Please wait while we fetch your file..."
           
        )
        
        # Copy the actual file
        await bot.copy_message(message.chat.id, chat_id, msg_id)
        
        # Send completion message
        await message.reply_text(
            f"✅ **Download Complete!**\n\n"
            f"File: {file_name}"
            f"⚠️Download it or Forward this files to saved msg it will delete with in 10 min!"
        )
        
    except Exception as e:
        await message.reply_text(
            f"❌ **Download Failed**\n\n"
            f"Error: {str(e)}\n"
            f"Please try again later or contact admin."
        )

async def send_batch(message, batch_id):
    """Send all files in a batch to user"""
    cursor.execute("SELECT batch_name, start_msg, end_msg FROM batches WHERE id=?", (batch_id,))
    row = cursor.fetchone()
    
    if not row:
        return await message.reply_text(
            "❌ **Not Found**\n\n"
            "The requested batch could not be found. It may have been deleted or the link is invalid."
        )
    
    batch_name, start, end = row
    file_count = end - start + 1
    
    # Send batch info
    await message.reply_text(
        f"📦 **Download Started**\n\n"
        f"📝 **Name:** {batch_name}\n"
        f"📁 **Files:** {file_count} files\n\n"
    )
    
    successful = 0
    failed = 0
    
    for msg_id in range(start, end + 1):
        try:
            await bot.copy_message(message.chat.id, DB_CHANNEL, msg_id)
            successful += 1
            await asyncio.sleep(0.5)  # Small delay to avoid flooding
        except:
            failed += 1
    
    # Send completion summary
    await message.reply_text(
        f"✅ **Download Complete!**\n\n"
        f"📦 **{batch_name}**\n"
        f"✅ **Downloaded:** {successful} files\n"
        f"❌ **Failed:** {failed} files\n\n"
        f"💡 Thank you for using our service!"
    )

# ------------------ BROADCAST SYSTEM ------------------

@bot.on_message(filters.private & filters.command("broadcast"))
async def broadcast_message(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ Only admins can broadcast messages.")
    
    if len(message.command) < 2:
        return await message.reply_text(
            "📢 **Broadcast Message**\n\n"
            "**Usage:** `/broadcast <your_message>`\n\n"
            "**Example:** `/broadcast 🎉 New files available! Check them out.`"
        )
    
    broadcast_text = " ".join(message.command[1:])
    
    # Get all users
    cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
    users = cursor.fetchall()
    
    if not users:
        return await message.reply_text("❌ No users found to broadcast to.")
    
    # Confirm broadcast
    buttons = [
        [InlineKeyboardButton("✅ Confirm Broadcast", callback_data=f"confirm_broadcast_{len(users)}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")]
    ]
    
    await message.reply_text(
        f"📢 **Confirm Broadcast**\n\n"
        f"**Message:** {broadcast_text}\n"
        f"**Recipients:** {len(users)} users\n\n"
        f"Are you sure you want to send this message to all users?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# Start the bot
if __name__ == "__main__":
    print("✅ Bot is now running and ready to serve!")
    bot.run()
