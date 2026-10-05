"""Gateway-mounted SmallWebRTC signaling and bounded media-session ownership."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner
from pydantic import BaseModel, ConfigDict, Field
from voice_platform_contracts.conversation import CallResponse
from voice_platform_llm import LLMSettings
from voice_platform_speech import SpeechSettings

from .backend import Backend, DependencyError
from .processor import CommandFrame, VoiceProcessor
from .qualified import QualifiedDialogue


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    call_id: UUID
    manual_turns: bool = False


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp: str = Field(min_length=1, max_length=65536)
    type: Literal["offer"] = "offer"
    pc_id: str | None = Field(default=None, max_length=128)
    restart_pc: bool = False


async def disconnect(connection: SmallWebRTCConnection) -> None:
    # Pipecat 1.12 retains a delayed renegotiation task but does not join it on close.
    delayed = getattr(connection, "_renegotiation_task", None)
    try:
        await connection.disconnect()  # type: ignore[no-untyped-call]
    finally:
        if isinstance(delayed, asyncio.Task):
            delayed.cancel()
            with suppress(asyncio.CancelledError):
                await delayed


class Session:
    def __init__(
        self,
        data: SessionCreate,
        backend: Backend,
        speech: SpeechSettings,
        llm: LLMSettings,
        providers: httpx.AsyncClient,
    ) -> None:
        self.id = uuid4()
        self.token = secrets.token_urlsafe(32)
        self.data = data
        self.backend = backend
        self.dialogue = QualifiedDialogue(
            backend, llm.build_router(providers), data.conversation_id, data.call_id
        )
        # STT deadlines include live input collection; reserve time for a bounded 30 s utterance.
        stt_settings = speech.model_copy(
            update={"policy": speech.policy.model_copy(update={"attempt_timeout_seconds": 45})}
        )
        self.processor = VoiceProcessor(
            self.dialogue,
            stt_settings.build_stt(),
            speech.build_tts(providers),
            self.notify,
            manual=data.manual_turns,
        )
        self.dialogue.notify = self.notify
        self.processor.finish_media = self.finish_media
        self.finish_task: asyncio.Task[None] | None = None
        self.connection: SmallWebRTCConnection | None = None
        self.runner: WorkerRunner | None = None
        self.worker: PipelineWorker | None = None
        self.task: asyncio.Task[None] | None = None
        self.call: CallResponse | None = None
        self.lock = asyncio.Lock()
        self.created = asyncio.get_running_loop().time()
        self.disconnected_at: float | None = None
        self.closed = False
        self.ending = False
        self.pc_id: str | None = None
        self.last_offer: Offer | None = None
        self.last_answer: dict[str, str] | None = None

    async def notify(self, payload: dict[str, object]) -> None:
        if self.connection is not None:
            self.connection.send_app_message(payload)

    async def finish_media(self) -> None:
        # EndFrame is ordered after synthesized audio; this is not a playback receipt.
        if self.worker is not None:
            await self.worker.queue_frame(EndFrame())

    async def prepare(self) -> None:
        live = await self.backend.live(self.data.conversation_id, self.id)
        if (
            live.active_call is None
            or live.active_call.id != self.data.call_id
            or live.conversation.state in {"COMPLETED", "FAILED"}
        ):
            raise DependencyError(409)
        self.call = live.active_call
        if self.call.status == "CREATED":
            self.call = await self.backend.call(
                self.data.conversation_id, self.call, "CONNECTING", self.id
            )
        elif self.call.status == "CONNECTED":
            self.call = await self.backend.call(
                self.data.conversation_id, self.call, "RECONNECTING", self.id, "runtime_attach"
            )
        current = live.conversation
        if current.state == "CREATED":
            await self.backend.state(self.data.conversation_id, current, "CONNECTING", self.id)

    async def connected(self) -> None:
        async with self.lock:
            if self.closed or self.ending:
                return
            try:
                assert self.call is not None
                if self.call.status in {"CONNECTING", "RECONNECTING"}:
                    try:
                        self.call = await self.backend.call(
                            self.data.conversation_id, self.call, "CONNECTED", self.id
                        )
                    except DependencyError:
                        live = await self.backend.live(self.data.conversation_id, self.id)
                        if live.active_call is None or live.active_call.id != self.data.call_id:
                            raise DependencyError(409) from None
                        self.call = live.active_call
                        if self.call.status != "CONNECTED":
                            self.call = await self.backend.call(
                                self.data.conversation_id, self.call, "CONNECTED", self.id
                            )
                current = await self.backend.conversation(self.data.conversation_id, self.id)
                if current.state == "CONNECTING":
                    await self.backend.state(
                        self.data.conversation_id, current, "GREETING", self.id
                    )
                await self.dialogue.recover()
                if self.dialogue.close_media_requested:
                    await self.finish_media()
                    return
                self.processor.ready = True
                self.disconnected_at = None
                await self.notify({"type": "connected", "call_id": str(self.data.call_id)})
                if self.worker is not None:
                    await self.worker.queue_frame(CommandFrame("greet"))
            except DependencyError:
                await self.notify(
                    {"type": "error", "code": "dependency_unavailable", "retry_required": True}
                )

    async def disconnected(self) -> None:
        await self.processor.halt()
        async with self.lock:
            if self.closed or self.ending:
                return
            self.disconnected_at = asyncio.get_running_loop().time()
            if self.call is not None and self.call.status == "CONNECTED":
                try:
                    self.call = await self.backend.call(
                        self.data.conversation_id,
                        self.call,
                        "RECONNECTING",
                        self.id,
                        "transport_disconnect",
                    )
                except DependencyError:
                    await self.notify({"type": "error", "code": "dependency_unavailable"})

    async def offer(self, data: Offer) -> dict[str, str]:
        # Route serialization is independent from transport callbacks' lifecycle lock.
        if self.closed or self.ending:
            raise DependencyError(409)
        if data == self.last_offer and self.last_answer is not None:
            return dict(self.last_answer)
        if self.connection is not None:
            if data.pc_id != self.pc_id:
                raise DependencyError(409)
            await self.processor.halt()
            async with self.lock:
                if self.call is not None and self.call.status == "CONNECTED":
                    self.call = await self.backend.call(
                        self.data.conversation_id,
                        self.call,
                        "RECONNECTING",
                        self.id,
                        "transport_renegotiate",
                    )
            await self.connection.renegotiate(data.sdp, data.type, restart_pc=data.restart_pc)
        else:
            if data.pc_id is not None:
                raise DependencyError(409)
            connection = SmallWebRTCConnection(ice_servers=[], connection_timeout_secs=30)
            self.connection = connection
            try:
                await connection.initialize(data.sdp, data.type)
                transport = SmallWebRTCTransport(
                    connection,
                    TransportParams(
                        audio_in_enabled=True,
                        audio_out_enabled=True,
                        audio_in_sample_rate=16000,
                        audio_out_sample_rate=24000,
                    ),
                )
                pipeline = Pipeline(
                    [
                        transport.input(),
                        VADProcessor(
                            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.4))
                        ),
                        self.processor,
                        transport.output(),
                    ]
                )
                worker = PipelineWorker(
                    pipeline,
                    params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
                    idle_timeout_secs=300,
                )
                self.worker = worker
                self.runner = WorkerRunner(handle_sigint=False)

                @transport.event_handler("on_client_connected")  # type: ignore[untyped-decorator]
                async def on_connected(_transport: Any, _connection: Any) -> None:
                    await self.connected()

                @transport.event_handler("on_client_disconnected")  # type: ignore[untyped-decorator]
                async def on_disconnected(_transport: Any, _connection: Any) -> None:
                    await self.disconnected()

                @transport.event_handler("on_app_message")  # type: ignore[untyped-decorator]
                async def on_message(_transport: Any, message: Any, _sender: Any) -> None:
                    if isinstance(message, dict) and message.get("type") in {
                        "start",
                        "stop",
                        "retry",
                    }:
                        await worker.queue_frame(CommandFrame(message["type"]))

                self.task = asyncio.create_task(self.run())
            except BaseException:
                await disconnect(connection)
                self.connection = None
                raise
        answer = self.connection.get_answer()  # type: ignore[no-untyped-call]
        if not isinstance(answer, dict):
            raise DependencyError()
        self.pc_id = answer["pc_id"]
        self.last_offer = data
        self.last_answer = dict(answer)
        return answer

    async def run(self) -> None:
        assert self.runner is not None and self.worker is not None
        try:
            await self.runner.add_workers(self.worker)
            await self.runner.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.notify({"type": "error", "code": "pipeline_failed"})
        finally:
            await self.processor.halt()
            if not self.closed:
                self.disconnected_at = asyncio.get_running_loop().time()
                if self.dialogue.close_media_requested and self.finish_task is None:
                    self.finish_task = asyncio.create_task(self.finish_workflow_media())

    async def finish_workflow_media(self) -> None:
        try:
            await self.close()
        except DependencyError:
            await self.notify(
                {"type": "error", "code": "dependency_unavailable", "retry_required": True}
            )

    async def close(self, *, failed: bool = False) -> None:
        self.ending = True
        try:
            async with self.lock:
                if self.closed:
                    return
                await self.processor.halt()
                if self.call is not None:
                    # Resolve a lost transition response with authoritative current state.
                    if self.call.status in {"CONNECTING", "RECONNECTING", "CONNECTED"}:
                        target = "FAILED" if failed or self.call.status != "CONNECTED" else "ENDED"
                        try:
                            self.call = await self.backend.call(
                                self.data.conversation_id,
                                self.call,
                                target,
                                self.id,
                                "runtime_timeout" if failed else "client_end",
                            )
                        except DependencyError:
                            live = await self.backend.live(self.data.conversation_id, self.id)
                            if (
                                live.active_call is not None
                                and live.active_call.id == self.data.call_id
                            ):
                                target = (
                                    "FAILED"
                                    if failed or live.active_call.status != "CONNECTED"
                                    else "ENDED"
                                )
                                self.call = await self.backend.call(
                                    self.data.conversation_id,
                                    live.active_call,
                                    target,
                                    self.id,
                                    "runtime_end",
                                )
                self.closed = True
        finally:
            # Durable closure can remain retryable; media resources must still be released.
            try:
                if self.runner is not None:
                    await self.runner.cancel()
                if self.task is not None:
                    with suppress(asyncio.CancelledError):
                        await self.task
            finally:
                if self.connection is not None:
                    await disconnect(self.connection)
                    self.connection = None


def create_app(conversation_url: str, token: str | None) -> FastAPI:
    sessions: dict[UUID, Session] = {}
    offer_locks: dict[UUID, asyncio.Lock] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        speech = SpeechSettings.from_env()
        llm = LLMSettings.from_env()
        async with (
            httpx.AsyncClient(
                base_url=conversation_url, timeout=5, follow_redirects=False
            ) as client,
            httpx.AsyncClient(follow_redirects=False) as providers,
        ):
            app.state.backend = Backend(client, token)
            app.state.providers = providers
            app.state.speech = speech
            app.state.llm = llm

            async def reap() -> None:
                while True:
                    await asyncio.sleep(10)
                    now = asyncio.get_running_loop().time()
                    for sid, session in list(sessions.items()):
                        if (
                            now - session.created > 1800
                            or (
                                session.disconnected_at is not None
                                and now - session.disconnected_at > 60
                            )
                            or (session.connection is None and now - session.created > 60)
                        ):
                            try:
                                await session.close(failed=True)
                            except DependencyError:
                                await session.processor.halt()
                                continue
                            sessions.pop(sid, None)
                            offer_locks.pop(sid, None)

            reaper = asyncio.create_task(reap())
            try:
                yield
            finally:
                reaper.cancel()
                with suppress(asyncio.CancelledError):
                    await reaper
                for session in list(sessions.values()):
                    try:
                        await session.close(failed=True)
                    except DependencyError:
                        # Media still has to close if the durable dependency is unavailable.
                        await session.processor.halt()
                        if session.runner is not None:
                            await session.runner.cancel()
                        if session.task is not None:
                            with suppress(asyncio.CancelledError):
                                await session.task
                        if session.connection is not None:
                            await disconnect(session.connection)
                sessions.clear()
                offer_locks.clear()

    app = FastAPI(title="Voice Runtime", lifespan=lifespan)
    # Keep third-party logs from recording transcript/SDP or provider responses.
    logger.disable("pipecat")

    @app.exception_handler(DependencyError)
    async def dependency_error(_request: Any, error: DependencyError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status, content={"detail": "voice dependency unavailable"}
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Any, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "invalid request"})

    def session_for(sid: UUID, authorization: str | None) -> Session:
        session = sessions.get(sid)
        if (
            session is None
            or authorization is None
            or not secrets.compare_digest(authorization, "Bearer " + session.token)
        ):
            raise HTTPException(404, "voice session not found")
        return session

    @app.get("/", response_class=HTMLResponse)
    async def page() -> str:
        return Path(__file__).with_name("client.html").read_text()

    @app.post("/sessions")
    async def create_session(data: SessionCreate) -> dict[str, object]:
        if len(sessions) >= 8 or any(
            s.data.conversation_id == data.conversation_id for s in sessions.values()
        ):
            raise HTTPException(409, "voice session already active or capacity reached")
        session = Session(
            data, app.state.backend, app.state.speech, app.state.llm, app.state.providers
        )
        # Reserve before awaiting network I/O to prevent competing media owners.
        sessions[session.id] = session
        offer_locks[session.id] = asyncio.Lock()
        try:
            await session.prepare()
        except BaseException:
            sessions.pop(session.id, None)
            offer_locks.pop(session.id, None)
            raise
        return {
            "session_id": str(session.id),
            "token": session.token,
            "speech_mode": app.state.speech.mode,
            "llm_mode": app.state.llm.mode,
        }

    @app.get("/sessions/{sid}/status")
    async def session_status(
        sid: UUID, authorization: str | None = Header(default=None)
    ) -> dict[str, object]:
        session = session_for(sid, authorization)
        return {
            "session_id": str(sid),
            "conversation_id": str(session.data.conversation_id),
            "call_id": str(session.data.call_id),
            "closed": session.closed,
            "ending": session.ending,
            "ready": session.processor.ready,
            "pending_operation": session.dialogue.pending is not None
            or session.dialogue.workflow_pending is not None,
            "media_state": session.connection.pc.connectionState
            if session.connection
            else "unattached",
            "expires_in_seconds": max(
                0, 1800 - (asyncio.get_running_loop().time() - session.created)
            ),
            "llm_mode": app.state.llm.mode,
            "speech_mode": app.state.speech.mode,
            "providers": {
                "llm": [asdict(h) for h in session.dialogue.llm.health()],
                "stt": [asdict(h) for h in session.processor.stt.health()],
                "tts": [asdict(h) for h in session.processor.tts.health()],
            },
            "scope": "session_process_local",
        }

    @app.post("/sessions/{sid}/offer")
    async def offer(
        sid: UUID, data: Offer, authorization: str | None = Header(default=None)
    ) -> dict[str, str]:
        session = session_for(sid, authorization)
        async with offer_locks[sid]:
            try:
                async with asyncio.timeout(20):
                    return await session.offer(data)
            except DependencyError:
                raise
            except (Exception, TimeoutError):
                await session.processor.halt()
                raise HTTPException(503, "voice negotiation failed") from None

    @app.post("/sessions/{sid}/retry")
    async def retry(sid: UUID, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        session = session_for(sid, authorization)
        if session.connection is None or session.connection.pc.connectionState != "connected":
            raise HTTPException(409, "reconnect media before retrying")
        await session.connected()
        return {"ready": session.processor.ready}

    @app.delete("/sessions/{sid}")
    async def end(sid: UUID, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        session = session_for(sid, authorization)
        await session.close()
        sessions.pop(sid, None)
        offer_locks.pop(sid, None)
        return {"ended": True}

    return app
