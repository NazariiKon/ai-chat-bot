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
        return "Ось досьє учасників чату:\n- Немає даних про учасників.\n\n"

    lines = ["Ось досьє учасників чату:"]
    for participant in participants:
        # Support both mapping and attribute-style objects
        if isinstance(participant, dict):
            first_name = participant.get("first_name") or "Користувач"
            username = participant.get("username") or "unknown"
            persona = participant.get("persona") or "Новий учасник."
            tg_id = participant.get("tg_id")
        else:
            first_name = getattr(participant, "first_name", None) or "Користувач"
            username = getattr(participant, "username", None) or "unknown"
            persona = getattr(participant, "persona", None) or "Новий учасник."
            tg_id = getattr(participant, "tg_id", None)

        username = str(username).lstrip("@") if username else "unknown"
        lines.append(f"- {first_name} (@{username}): {persona}")

    return "\n".join(lines) + "\n\n"


def build_addressing_hint() -> str:
    """Return a consistent instruction that the model should answer only the last message."""
    return (
        "Важливо: відповідай ТІЛЬКИ на останнє повідомлення (те, яке спричинило цей виклик). "
        "Використовуй попередні повідомлення лише як фон для розуміння контексту, але не відповідай на старі питання, "
        "якщо вони не адресовані прямо в поточному повідомленні.\n\n"
    )


def build_system_prompt(display_name: str, bot_style: str, personas_context: str) -> str:
    """Build the full system prompt used for every AI request."""
    return (
        f"Тебе звати {display_name}. Відповідай українською.\n"
        "ТВОЯ ПОЗИЦІЯ: відповідай точно, ввічливо і по суті.\n"
        f"ТВОЯ ПОВЕДІНКА ЗАРАЗ: {bot_style}\n"
        "Важливо: цей стиль впливає лише на тон, манеру і емоційність відповіді, але НЕ на факти, знання або правильність інформації.\n\n"

        "ДОСТУПНІ КОРИСТУВАЦЬКІ КОМАНДИ (ТІЛЬКИ ПРО НИХ МОЖНА КАЗАТИ):\n"
        "- /whoami - Показує, що ти знаєш про користувача.\n"
        "- /forget_me - Видаляє всі дані про користувача.\n"
        "- /bot_info - Твій поточний нікнейм та опис стилю.\n\n"

        "СЕРЕДОВИЩЕ ТА ТЕХНІЧНІ ТЕГИ (ОБОВ'ЯЗКОВО ДО ВИКОРИСТАННЯ):\n"
        "Ці теги — це твої команди для БАЗИ ДАНИХ. Ти маєш ВКЛЮЧАТИ їх у свій вивід, щоб система зафіксувала зміни. "
        "Але сам текст тегів буде автоматично видалено перед тим, як користувач побачить повідомлення.\n"
        "1. [MEMORY_UPDATE: факт] — Фіксуй нові знання про людей тільки якщо вони справді важливі для майбутнього спілкування. "
        "Один ключовий факт за відповідь достатньо.\n"
        "   ВАЖЛИВО: Не зберігай банальні, повторювані або тимчасові деталі. Пиши лише ті спостереження, які показують характер, інтереси, потреби або тон користувача.\n"
        "2. [MEMORY_REMOVE: ключ] — Видаляй застарілу або неточну інформацію, коли користувач про це просить.\n"
        "3. [NAME_UPDATE: ім'я] — Змінюй своє ім'я.\n"
        "4. [STYLE_UPDATE: детальний_стиль] — Змінюй свою поведінку.\n\n"

        "ПРИКЛАД ПРАВИЛЬНОЇ ВІДПОВІДІ (ти пишеш так):\n"
        "Користувач: Тебе звати Боб, будь піратом.\n"
        "Твоя відповідь: [NAME_UPDATE: Боб] [STYLE_UPDATE: Грубий пірат з папугою] Йо-хо-хо, я тепер Боб!\n\n"

        "СУВОРА ЗАБОРОНА ПОЯСНЕНЬ:\n"
        "НІКОЛИ не пояснюй користувачу, що ти 'використовуєш теги' або 'оновлюєш базу'. "
        "Користувач не повинен знати про існування [MEMORY_UPDATE] чи інших тегів в дужках. Просто використовуй їх мовчки.\n\n"

        "ПРАВИЛА ЕТИКЕТУ:\n"
        "- Тобі надано список учасників чату та їхні @username. Використовуй теги (@username), коли хочеш звернутися до когось, залучити до розмови або відповісти на питання.\n"
        "- Використовуй емодзі 😊\n"
        "- Веди себе природно.\n\n"
        + build_addressing_hint()
        + personas_context
    )


def sanitize_context_message(content: str) -> str:
    """Sanitize older chat history so it remains context without inviting an answer."""
    if not content:
        return ""

    text = content.strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def summarize_context_message(content: str, max_words: int = 6) -> str:
    """Create a short gist of an older message for history context."""
    sanitized = sanitize_context_message(content)
    if not sanitized:
        return ""

    words = sanitized.split()
    if len(words) <= max_words:
        return sanitized

    return " ".join(words[:max_words]) + "..."


def build_history_context(
    history: List[Dict[str, str]],
    max_items: int = 12,
    max_words: int = 12,
) -> str:
    """Build a safe summary of older history entries used only as context."""
    if not history or len(history) <= 1:
        return ""

    items = history[:-1][-max_items:]
    lines: List[str] = []
    for entry in items:
        content = summarize_context_message(entry.get("content", ""), max_words=max_words)
        if content:
            lines.append(f"- {content}")

    if not lines:
        return ""

    return (
        "ПОПЕРЕДНІ ТЕМИ ЧАТУ (не відповідай на них прямо):\n"
        + "\n".join(lines)
        + "\n\n"
    )


def build_messages(
    history: List[Dict[str, str]],
    system_prompt: str,
    max_items: int = 8,
    max_words: int = 6,
) -> List[Dict[str, str]]:
    """Build the full message list for the AI model from history and the system prompt."""
    messages = [{"role": "system", "content": system_prompt}]

    if not history:
        return messages

    history_context = build_history_context(history, max_items=max_items, max_words=max_words)
    if history_context:
        messages.append({"role": "system", "content": history_context})

    last_message = history[-1]
    messages.append({"role": last_message["role"], "content": last_message["content"]})
    return messages
