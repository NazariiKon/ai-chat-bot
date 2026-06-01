import re
from typing import List, Dict, Any

HISTORY_QUESTION_PREFIX_RE = re.compile(
    r"^(чому|що|яка|який|які|хто|як|коли|де|навіщо|скільки)[\s,:-]*", 
    flags=re.IGNORECASE,
)


def build_personas_context(participants: List[Any]) -> str:
    """Create a chat persona context block for the system prompt.

    Accepts ORM objects or plain dicts. Safely extracts `first_name`, `username`,
    and `persona`, normalizes username (no leading @) and returns a readable block.
    """
    if not participants:
        return "Учасники чату: дані відсутні.\n\n"

    lines = ["Учасники чату та що ти про них знаєш:"]
    for participant in participants:
        # Support both mapping and attribute-style objects
        if isinstance(participant, dict):
            first_name = participant.get("first_name") or "Користувач"
            username = participant.get("username") or "unknown"
            persona = participant.get("persona") or "Поки нічого не відомо."
            tg_id = participant.get("tg_id")
        else:
            first_name = getattr(participant, "first_name", None) or "Користувач"
            username = getattr(participant, "username", None) or "unknown"
            persona = getattr(participant, "persona", None) or "Поки нічого не відомо."
            tg_id = getattr(participant, "tg_id", None)

        username = str(username).lstrip("@") if username else "unknown"
        lines.append(f"- {first_name} (@{username}): {persona}")

    return "\n".join(lines) + "\n\n"


def build_system_prompt(display_name: str, bot_style: str, personas_context: str) -> str:
    """Build the full system prompt used for every AI request."""
    return (
        # --- Identity ---
        f"Ти — {display_name}. Відповідай українською мовою.\n\n"

        # --- Persona / Style ---
        f"Твій стиль спілкування: {bot_style}\n"
        "Цей стиль впливає ТІЛЬКИ на тон, манеру та емоційність. "
        "Він ніколи не впливає на точність фактів та правильність відповідей.\n\n"

        # --- Core behavior ---
        "ОСНОВНІ ПРАВИЛА ПОВЕДІНКИ:\n"
        "1. Будь точною, ввічливою і по суті.\n"
        "2. Використовуй емодзі помірно, щоб додати живості.\n"
        "3. Веди себе природно — ти частина компанії, а не робот.\n"
        "4. Коли звертаєшся до когось, використовуй @username.\n\n"

        # --- Context handling (CRITICAL FIX) ---
        "РОБОТА З КОНТЕКСТОМ ЧАТУ:\n"
        "Перед твоїм повідомленням ти отримуєш історію останніх повідомлень чату. "
        "Це потрібно, щоб ти розуміла контекст розмови.\n"
        "- ВІДПОВІДАЙ лише на ОСТАННЄ повідомлення — те, яке безпосередньо спричинило цей виклик.\n"
        "- Використовуй попередні повідомлення ТІЛЬКИ як фон для розуміння контексту.\n"
        "- ЯКЩО в останньому повідомленні тебе ПРЯМО запитують про щось зі старих повідомлень "
        "(наприклад: «це правда що Рома написав?», «що він мав на увазі?») — "
        "тоді знайди відповідне повідомлення в історії і дай відповідь.\n"
        "- НЕ відповідай на старі питання і НЕ коментуй старі повідомлення за власною ініціативою.\n\n"

        # --- Available user commands ---
        "КОМАНДИ КОРИСТУВАЧІВ (тільки про них можна розповідати):\n"
        "- /whoami — показує, що ти знаєш про користувача\n"
        "- /forget_me — видаляє всі дані про користувача\n"
        "- /bot_info — твій поточний нікнейм та опис стилю\n\n"

        # --- Technical tags ---
        "ТЕХНІЧНІ ТЕГИ (обов'язково до використання):\n"
        "Ці теги — твої команди для бази даних. Включай їх у відповідь коли потрібно. "
        "Текст тегів автоматично видаляється перед показом користувачу.\n"
        "• [MEMORY_UPDATE: факт] — зберегти важливий факт про людину "
        "(лише справді важливі речі: характер, інтереси, потреби).\n"
        "• [MEMORY_REMOVE: ключ] — видалити застарілу/неточну інформацію.\n"
        "• [NAME_UPDATE: ім'я] — змінити своє ім'я.\n"
        "• [STYLE_UPDATE: стиль] — змінити свою поведінку/стиль.\n\n"

        "Приклад:\n"
        "Користувач: Тебе звати Боб, будь піратом.\n"
        "Відповідь: [NAME_UPDATE: Боб] [STYLE_UPDATE: Грубий пірат з папугою] Йо-хо-хо, тепер я Боб! 🏴‍☠️\n\n"

        "ЗАБОРОНА: НІКОЛИ не згадуй теги, базу даних чи технічні деталі у відповідях. "
        "Просто використовуй теги мовчки.\n\n"

        # --- Participants ---
        + personas_context
    )


def build_messages(
    history: List[Dict[str, str]],
    system_prompt: str,
    reply_context: str | None = None,
) -> List[Dict[str, str]]:
    """Build the full message list for the AI model.
    
    History messages are passed as-is (full text) to give the model real
    conversation context. The system prompt already instructs the model
    to only respond to the last message.
    
    If reply_context is provided, it contains the text of the message
    the user replied to (which may not be in the recent history).
    """
    messages = [{"role": "system", "content": system_prompt}]

    if not history:
        return messages

    # All history messages except the last one are context.
    # We prepend a system note reminding the model these are context-only.
    if len(history) > 1:
        context_messages = history[:-1]
        
        # Build a context block with full message text
        context_lines = []
        for entry in context_messages:
            role_label = "Бот" if entry["role"] == "assistant" else "Користувач"
            context_lines.append(f"{role_label}: {entry['content']}")
        
        context_block = (
            "Ось останні повідомлення чату (ТІЛЬКИ ДЛЯ КОНТЕКСТУ, не відповідай на них):\n"
            + "\n".join(context_lines)
        )
        messages.append({"role": "system", "content": context_block})

    # If the user replied to a specific message, inject it so the model sees it
    if reply_context:
        messages.append({
            "role": "system",
            "content": (
                "Користувач відповідає (reply) на наступне повідомлення. "
                "Враховуй його при формуванні відповіді:\n"
                + reply_context
            ),
        })

    # The actual message to respond to
    last_message = history[-1]
    messages.append({"role": last_message["role"], "content": last_message["content"]})
    return messages
