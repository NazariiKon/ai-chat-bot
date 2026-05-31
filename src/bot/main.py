from telegram.ext import Application
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from bot.config import settings
from bot.handlers.start import start_command
from bot.handlers.message import handle_message
from bot.database.session import init_db

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def post_init(application: Application) -> None:
    """Initialize database before starting the bot."""
    from bot.database.session import init_db
    await init_db()
    print("Database initialized successfully.")

def main() -> None:
    if not settings.BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing in .env")

    from bot.handlers.commands import (
        whoami_command,
        forget_me_command,
        bot_info_command,
        set_response_chance_command,
    )

    # Build the application with post_init
    app = ApplicationBuilder() \
        .token(settings.BOT_TOKEN) \
        .post_init(post_init) \
        .build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("forget_me", forget_me_command))
    app.add_handler(CommandHandler("bot_info", bot_info_command))
    app.add_handler(CommandHandler("set_response_chance", set_response_chance_command))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.ALL & ~filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is starting polling...")
    app.run_polling()

if __name__ == "__main__":
    main()