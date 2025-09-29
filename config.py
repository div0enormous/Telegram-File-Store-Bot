# config.py - Enhanced Configuration

# Telegram API Credentials
API_ID = 20338805
API_HASH = "665da84b46a96d6ad9130ef2e764730e"
BOT_TOKEN = "7549567390:AAFl59W2yY-xh7FQMO8ank7XIOtpaIo2QZk"

# MongoDB (Keep for future use)
DATABASE_URL = "mongodb+srv://teamads299792458_db_user:YourPassword@cluster0.q8iquia.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DATABASE_NAME = "FileStoreDB"

# Bot Configuration
DB_CHANNEL = -1003068444789      # Your private database channel
ADMINS = [1500034181]            # List of admin user IDs

# Bot Settings
BOT_NAME = "Professional File Store Bot"
BOT_VERSION = "2.0"
SUPPORT_CHAT = "@your_support_chat"  # Optional: Your support chat username

# File Upload Settings
MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2GB in bytes (Telegram limit)
ALLOWED_FILE_TYPES = [
    'document', 'video', 'audio', 'photo', 
    'voice', 'video_note', 'sticker', 'animation'
]

# Batch Settings
MAX_BATCH_SIZE = 1000  # Maximum files per batch
BATCH_DELAY = 0.5      # Delay between file sends (seconds)

# Advanced Settings
AUTO_DELETE_AFTER_DAYS = 0.1    # 0 means never delete, set number of days for auto-deletion
LOG_CHANNEL = None            # Optional: Channel to log bot activities
FORCE_SUB_CHANNEL = None      # Optional: Channel users must join before accessing files

# Database File
DB_FILE = "files.db"

# Welcome Messages
USER_WELCOME_TEXT = """
👋 **Welcome {name}!**

🗂️ **Professional File Store Bot**

This bot helps you access files through secure links. 
All files are safely stored and can be accessed anytime with valid links.

💡 **Need a file?** Just click on any file link shared with you!
"""

ADMIN_WELCOME_TEXT = """
🛡️ **Admin Panel - Welcome {name}!**

📊 **Quick Stats:**
• Files: `{total_files}`
• Batches: `{total_batches}`
• Users: `{total_users}`

📤 **Upload Files:** Just send any media file
📦 **Create Batch:** Use batch mode for multiple files

Choose an option below:
"""

# Help Text
HELP_TEXT = """
📖 **How to Use This Bot**

🔸 **For Users:**
• Click on file links to download
• All file types are supported
• Links work permanently

🔸 **File Types Supported:**
• Documents (PDF, DOC, etc.)
• Videos (MP4, MKV, etc.)
• Photos (JPG, PNG, etc.)
• Audio files (MP3, etc.)
• Voice messages
• Stickers & Animations
• And much more!
"""

ABOUT_TEXT = """
🤖 **Professional File Store Bot**

🔹 **Version:** 2.0 Professional
🔹 **Features:**
• All media types support
• Batch upload/download
• User management
• Admin controls
• Professional UI

🔹 **Built with:** Pyrogram + SQLite
🔹 **Status:** Fully Operational ✅
"""