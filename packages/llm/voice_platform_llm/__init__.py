"""Module 8 LLM boundary, deterministic local provider, and test adapter."""

from .contracts import (
    CompletionEvent,
    ContextMessage,
    GenerationRequest,
    GenerationResponse,
    PromptContext,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolDefinition,
    Usage,
)
from .mock import MockLLMProvider, MockScript
from .openai import OpenAILLMProvider
from .openrouter import OpenRouterLLMProvider
from .provider import LLMError, LLMProvider, LLMStream
from .router import (
    LLMRouter,
    ProviderCapabilities,
    ProviderHealthSnapshot,
    ProviderSlot,
    RouterPolicy,
)
from .runtime import ConversationLLMRuntime
from .settings import LLMSettings

__all__ = [
    "CompletionEvent",
    "ContextMessage",
    "ConversationLLMRuntime",
    "GenerationRequest",
    "GenerationResponse",
    "LLMError",
    "LLMProvider",
    "LLMStream",
    "LLMRouter",
    "LLMSettings",
    "MockLLMProvider",
    "MockScript",
    "OpenAILLMProvider",
    "OpenRouterLLMProvider",
    "ProviderCapabilities",
    "ProviderHealthSnapshot",
    "ProviderSlot",
    "RouterPolicy",
    "PromptContext",
    "StreamEvent",
    "TextDelta",
    "ToolCall",
    "ToolCallEvent",
    "ToolDefinition",
    "Usage",
]
