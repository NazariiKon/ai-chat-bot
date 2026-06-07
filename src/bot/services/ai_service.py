import importlib
import logging
from typing import Optional
from bot.config import settings

class BaseAiService:
    async def get_response(self, messages: list[dict[str, str]]) -> str:
        raise NotImplementedError

class GroqAiService(BaseAiService):
    def __init__(self):
        groq = importlib.import_module("groq")
        self.client = groq.AsyncGroq(
            api_key=settings.GROQ_API_KEY,
            max_retries=0,
        )
        self.model = settings.MODEL_NAME

    async def get_response(self, messages: list[dict[str, str]]) -> str:
        completion = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )
        return completion.choices[0].message.content

class GeminiAiService(BaseAiService):
    def __init__(self):
        gemini = importlib.import_module("google.generativeai")
        gemini.configure(api_key=settings.GEMINI_API_KEY)
        self.genai = gemini
        self.model = gemini.GenerativeModel(settings.GEMINI_MODEL)

    async def get_response(self, messages: list[dict[str, str]]) -> str:
        content_types = self.genai.types.content_types
        history = []
        for m in messages:
            role = m["role"]
            if role == "assistant":
                role = "model"
            elif role == "system":
                role = "user"

            history.append(
                content_types.to_content(
                    {
                        "role": role,
                        "parts": [
                            {
                                "text": m["content"],
                            }
                        ],
                    }
                )
            )

        if hasattr(self.model, "generate_content_async"):
            completion = await self.model.generate_content_async(contents=history)
        else:
            raise RuntimeError("Gemini async API is not available in the installed library")

        if hasattr(completion, "text") and completion.text:
            return completion.text
        if hasattr(completion, "candidates") and completion.candidates:
            candidate = completion.candidates[0]
            return getattr(candidate, "content", getattr(candidate, "text", ""))
        raise RuntimeError("Gemini response format is not supported")

class FallbackAiService(BaseAiService):
    def __init__(self, primary: BaseAiService, secondary: Optional[BaseAiService] = None):
        self.primary = primary
        self.secondary = secondary

    async def get_response(self, messages: list[dict[str, str]]) -> str:
        try:
            return await self.primary.get_response(messages)
        except Exception as primary_error:
            logging.warning(f"Primary AI provider failed: {primary_error}")
            if self.secondary:
                logging.info("Attempting fallback to secondary AI provider.")
                try:
                    response = await self.secondary.get_response(messages)
                    logging.info("Fallback AI provider succeeded.")
                    return response
                except Exception as secondary_error:
                    logging.error(f"Fallback AI provider also failed: {secondary_error}")
                    raise
            raise

class AIServiceFactory:
    @staticmethod
    def _try_initialize(name: str, service_class: type[BaseAiService]) -> Optional[BaseAiService]:
        try:
            return service_class()
        except ModuleNotFoundError as e:
            logging.warning(f"AI provider '{name}' unavailable: {e}")
        except Exception as e:
            logging.warning(f"AI provider '{name}' failed to initialize: {e}")
        return None

    @staticmethod
    def create_service() -> BaseAiService:
        provider = settings.AI_PROVIDER.lower().strip()

        if provider == "auto":
            gemini_service = AIServiceFactory._try_initialize("gemini", GeminiAiService)
            groq_service = AIServiceFactory._try_initialize("groq", GroqAiService)

            if gemini_service and groq_service:
                return FallbackAiService(gemini_service, groq_service)
            if gemini_service:
                return gemini_service
            if groq_service:
                return groq_service

            raise RuntimeError(
                "No AI provider is available. Install Groq or Gemini client and set API keys."
            )

        if provider == "groq":
            service = AIServiceFactory._try_initialize("groq", GroqAiService)
            if service:
                return service
            raise RuntimeError("Groq provider is not available or failed to initialize.")

        if provider == "gemini":
            service = AIServiceFactory._try_initialize("gemini", GeminiAiService)
            if service:
                return service
            raise RuntimeError("Gemini provider is not available or failed to initialize.")

        raise ValueError(f"Unsupported AI_PROVIDER: {settings.AI_PROVIDER}")

try:
    ai_service = AIServiceFactory.create_service()
except Exception as e:
    logging.error(f"Failed to initialize AI service: {e}")
    ai_service = None


async def parse_persona_patch(raw_text: str) -> dict:
    """Ask the AI to convert arbitrary text into a persona JSON patch.

    Returns a dict if parsing succeeds, otherwise returns {"notes": raw_text}.
    """
    import re, json
    if not ai_service:
        return {"notes": raw_text}

    raw_text = raw_text.replace("\\n", " ").replace("\n", " ").strip()

    system = (
        "You will receive a short piece of text that contains suggested updates "
        "for a user's persona. Output ONLY a single valid JSON object (no extra text).\n"
        "Include only fields you can infer clearly from the text.\n"
        "Do not include fields with empty objects, empty arrays, null, or blank values.\n"
        "Do not rewrite the user's existing alias or archetype unless the text explicitly asks for a new name or a new archetype.\n"
        "If the text is a simple self-description or skill statement, prefer:\n"
        "- traits\n"
        "- expertise_cluster.primary\n"
        "- notes\n"
        "Only use `alias` when the user gives a name, nickname, or direct rename request.\n"
        "Only use `archetype` when the user explicitly describes a long-term role or persona category, not a simple ability or attitude.\n"
        "Map freeform content into these fields when possible:\n"
        "- alias (string)\n"
        "- archetype (string)\n"
        "- traits (array of short strings)\n"
        "- expertise_cluster: { primary: [...], secondary: [...] }\n"
        "- behavioral_patterns (object)\n"
        "- loyalty_metrics (object)\n"
        "- vulnerabilities (string)\n"
        "- notes (string)\n"
        "When removing items from an existing list, use strings prefixed with a single dash (`-`) inside that list. Example: {\"traits\": [\"-system_tester\"]}.\n"
        "Always output all JSON field names and values in English. If the input is Ukrainian or any other language, translate it into clear English terms.\n"
        "Do not output any values in Ukrainian or Cyrillic inside the JSON object. If the user wrote in Ukrainian, translate the meaning into English values.\n"
        "If you cannot map content to any of the above, put it into `notes`.\n"
        "Return only valid JSON."
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": raw_text},
    ]

    def clean_patch(value):
        if isinstance(value, dict):
            cleaned = {}
            for k, v in value.items():
                cleaned_value = clean_patch(v)
                if cleaned_value is not None:
                    cleaned[k] = cleaned_value
            return cleaned if cleaned else None
        if isinstance(value, list):
            cleaned_list = [clean_patch(v) for v in value]
            cleaned_list = [v for v in cleaned_list if v is not None]
            return cleaned_list if cleaned_list else None
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


    try:
        resp = await ai_service.get_response(messages)
        # Try direct JSON parse
        try:
            parsed = json.loads(resp)
        except Exception:
            # Extract first {...} block
            m = re.search(r"\{[\s\S]*\}", resp)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    return {"notes": raw_text}
            else:
                return {"notes": raw_text}

        if not isinstance(parsed, dict):
            return {"notes": raw_text}

        def should_set_alias_archetype(text: str) -> bool:
            text = text.lower()
            triggers = [
                "називай мене",
                "зови мене",
                "мене звати",
                "мені ім'я",
                "my name",
                "call me",
                "alias",
                "ім'я",
            ]
            return any(trigger in text for trigger in triggers)

        if not should_set_alias_archetype(raw_text):
            parsed.pop("alias", None)
            parsed.pop("archetype", None)

        cleaned = clean_patch(parsed)
        return cleaned if cleaned else {"notes": raw_text}
    except Exception:
        return {"notes": raw_text}
