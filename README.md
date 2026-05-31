AI Chat Bot
===========

Simple Telegram AI chat bot implemented in Python.

Project layout
- `pyproject.toml` - project metadata and dependencies
- `src/bot/` - bot source code
  - `handlers/` - Telegram handlers
  - `services/` - AI and database services
  - `database/` - DB models and session
  - `config.py` - configuration loader

Quick start
1. Create a Python virtual environment and activate it.

   Windows PowerShell:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies. If you have `requirements.txt`:

   ```powershell
   pip install -r requirements.txt
   ```

   Or (if using `pyproject.toml` / Poetry):

   ```powershell
   pip install -e .
   ```

3. Create a `.env` file in the project root with required environment variables (example):

   BOT_TOKEN=your_telegram_bot_token
   BOT_USERNAME=your_bot_username
   GROQ_API_KEY=your_groq_api_key
   GEMINI_API_KEY=your_gemini_api_key
   AI_PROVIDER=auto
   MODEL_NAME=desired_model_name
   GEMINI_MODEL=desired_gemini_model
   DATABASE_URL=sqlite:///./bot.db
   MAX_MESSAGE_AGE_SECONDS=60

4. Run the bot:

   ```powershell
   python -m bot.main
   ```

Notes
- The bot stores chat history and persona facts in a database configured via `DATABASE_URL`.
- Behavior and AI provider selection are controlled via environment variables.
- This README is minimal; see code comments and `src/bot/config.py` for details about configuration keys.

Contributing
- Please run tests (if present) and linting before creating PRs.
- Keep secrets out of the repository; use `.env` which is ignored by `.gitignore`.

License
- No license specified. Add a `LICENSE` file if needed.
