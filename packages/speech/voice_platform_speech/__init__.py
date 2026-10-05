"""Provider-independent speech streams and credential-free local fixtures."""

from .contracts import (
    AudioChunk,
    AudioEvent,
    AudioFormat,
    SynthesisCompleted,
    SynthesisEvent,
    SynthesisRequest,
    TranscriptEvent,
    TranscriptionCompleted,
    TranscriptionEvent,
    TranscriptionRequest,
)
from .http_tts import RumikTTSProvider, SarvamTTSProvider
from .mock import (
    MockSTTProvider,
    MockSTTScript,
    MockTTSProvider,
    MockTTSScript,
    fixture_audio,
    fixture_pcm,
)
from .provider import ErrorCode, SpeechError, SpeechStream, STTProvider, TTSProvider
from .router import (
    SpeechCapabilities,
    SpeechHealth,
    SpeechRouterPolicy,
    SpeechSlot,
    STTRouter,
    TTSRouter,
)
from .runtime import SpeechToTextRuntime, TextToSpeechRuntime
from .sarvam_stt import SarvamSTTProvider
from .settings import SpeechConfigurationError, SpeechSettings

__all__ = [
    "RumikTTSProvider",
    "SarvamTTSProvider",
    "SarvamSTTProvider",
    "SpeechCapabilities",
    "SpeechHealth",
    "SpeechRouterPolicy",
    "SpeechSlot",
    "STTRouter",
    "TTSRouter",
    "SpeechConfigurationError",
    "SpeechSettings",
    "AudioChunk",
    "AudioEvent",
    "AudioFormat",
    "ErrorCode",
    "MockSTTProvider",
    "MockSTTScript",
    "MockTTSProvider",
    "MockTTSScript",
    "STTProvider",
    "SpeechError",
    "SpeechStream",
    "SpeechToTextRuntime",
    "SynthesisCompleted",
    "SynthesisEvent",
    "SynthesisRequest",
    "TTSProvider",
    "TextToSpeechRuntime",
    "TranscriptEvent",
    "TranscriptionCompleted",
    "TranscriptionEvent",
    "TranscriptionRequest",
    "fixture_audio",
    "fixture_pcm",
]
