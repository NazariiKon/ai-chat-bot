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
                try:
                    return await self.secondary.get_response(messages)
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
