"""Production Next.js → Gateway → actual domain services/PostgreSQL + browser WebRTC."""

import asyncio
import os
import re
import shutil
import socket
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import uvicorn
from api_gateway.app import create_app as create_gateway_app
from api_gateway.client import ConversationServiceClient, LeadServiceClient
from api_gateway.settings import GatewaySettings
from conversation_service.client import LeadServiceUnavailableError
from fastapi import FastAPI
from sqlalchemy import delete, func, select
from starlette.routing import Mount
from test_conversation_service import System
from test_conversation_service import system as system
from voice_platform_db.models import (
    Call,
    Conversation,
    DomainEvent,
    Handoff,
    Lead,
    LeadScoreHistory,
    Message,
    QualificationProfile,
)
from voice_platform_llm import (
    CompletionEvent,
    GenerationRequest,
    LLMRouter,
    LLMSettings,
    ProviderSlot,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from voice_platform_runtime.backend import Backend
from voice_platform_speech import (
    MockSTTProvider,
    MockSTTScript,
    SpeechSettings,
    SpeechSlot,
    STTRouter,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
other_system = system


class DashboardProvider:
    name = "mock-dashboard"
    model = "scenario-v1"

    async def stream(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        latest = next(
            (m.content for m in reversed(request.context.messages) if m.role == "user"), ""
        )
        if request.tools[0].name == "propose_qualification":
            proposals = [{"field_key": "education_level", "value": "masters", "evidence": latest}]
            yield ToolCallEvent(
                call=ToolCall(
                    id="facts",
                    name="propose_qualification",
                    arguments=cast(Any, {"proposals": proposals if "masters" in latest else []}),
                )
            )
            yield CompletionEvent(finish_reason="tool_calls")
        elif "speak to a human" in latest:
            yield ToolCallEvent(
                call=ToolCall(
                    id="handoff",
                    name="request_workflow_action",
                    arguments={"action": "HUMAN_HANDOFF", "evidence": latest},
                )
            )
            yield CompletionEvent(finish_reason="tool_calls")
        else:
            yield TextDelta(text="I can help collect your details.")
            yield CompletionEvent(finish_reason="stop")


async def wait_for_server(url: str) -> None:
    async with httpx.AsyncClient() as client:
        async with asyncio.timeout(30):
            while True:
                try:
                    if (await client.get(url)).status_code == 200:
                        return
                except httpx.RequestError:
                    pass
                await asyncio.sleep(0.1)


@pytest_asyncio.fixture
async def dashboard(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[str, MockSTTProvider]]:
    if os.getenv("RUN_DASHBOARD_BROWSER_TESTS") != "1":
        pytest.skip("opt in with RUN_DASHBOARD_BROWSER_TESTS=1 after npm run dashboard:build")
    root = Path(__file__).resolve().parents[2]
    if not (root / "apps/dashboard/.next/BUILD_ID").exists():
        pytest.fail("Build the production dashboard before running browser integration")
    monkeypatch.setenv("VOICE_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("SPEECH_MODE", "mock")
    monkeypatch.setenv("DEMO_FAULTS_ENABLED", "1")
    stt = MockSTTProvider(MockSTTScript(text="I confirm my masters degree.", interim=None))

    def llm_router(self: LLMSettings, client: httpx.AsyncClient) -> LLMRouter:
        return LLMRouter((ProviderSlot(DashboardProvider()),))

    def stt_router(self: SpeechSettings, **kwargs: Any) -> STTRouter:
        return STTRouter((SpeechSlot(stt),))

    monkeypatch.setattr(LLMSettings, "build_router", llm_router)
    monkeypatch.setattr(SpeechSettings, "build_stt", stt_router)
    gateway = create_gateway_app(
        settings=GatewaySettings("test", "http://lead", "test-token", 5),
        client=LeadServiceClient(system.lead, service_auth_token="test-token"),
        conversation_client=ConversationServiceClient(
            system.conversation, service_auth_token="test-token"
        ),
    )
    voice = cast(FastAPI, next(route.app for route in gateway.routes if isinstance(route, Mount)))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    gateway_port = listener.getsockname()[1]
    port_socket = socket.socket()
    port_socket.bind(("127.0.0.1", 0))
    dashboard_port = port_socket.getsockname()[1]
    port_socket.close()
    server = uvicorn.Server(
        uvicorn.Config(gateway, access_log=False, log_level="critical", lifespan="off")
    )
    async with gateway.router.lifespan_context(gateway):
        voice.state.backend = Backend(system.conversation, "test-token")
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        node = os.getenv("DASHBOARD_NODE") or shutil.which("node")
        assert node is not None
        process = await asyncio.create_subprocess_exec(
            node,
            str(root / "apps/dashboard/.next/standalone/apps/dashboard/server.js"),
            cwd=str(root / "apps/dashboard"),
            env=os.environ
            | {
                "GATEWAY_URL": f"http://127.0.0.1:{gateway_port}",
                "NEXT_TELEMETRY_DISABLED": "1",
                "PORT": str(dashboard_port),
                "HOSTNAME": "127.0.0.1",
            },
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await wait_for_server(f"http://127.0.0.1:{dashboard_port}/")
            yield f"http://127.0.0.1:{dashboard_port}/?lead={system.lead_id}", stt
        finally:
            process.terminate()
            await asyncio.wait_for(process.wait(), 10)
            server.should_exit = True
            await asyncio.wait_for(server_task, 10)
            listener.close()
    async with system.sessions.begin() as db:
        await db.execute(
            delete(DomainEvent).where(
                DomainEvent.payload["conversation_id"].astext == str(system.conversation_id)
            )
        )
        await db.execute(delete(Handoff).where(Handoff.conversation_id == system.conversation_id))


async def test_production_dashboard_voice_score_reconnect_lost_offer_and_handoff(
    system: System,
    dashboard: tuple[str, MockSTTProvider],
) -> None:
    from playwright.async_api import async_playwright, expect

    url, stt = dashboard
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.getenv(
                "CHROME_EXECUTABLE", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ),
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        try:
            page = await browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            lost_once = False
            offers: list[str | None] = []

            async def lose_offer(route: Any) -> None:
                nonlocal lost_once
                offers.append(route.request.post_data)
                response = await route.fetch()
                if not lost_once:
                    lost_once = True
                    await route.fulfill(status=503, json={"detail": "simulated lost reply"})
                else:
                    await route.fulfill(response=response)

            await page.route("**/api/voice/sessions/*/offer", lose_offer)
            await page.goto(url)
            await expect(
                page.get_by_role("heading", name="Conversation Integration Lead")
            ).to_be_visible()
            await page.get_by_role("button", name="Start / resume call", exact=False).click()
            await expect(page.get_by_role("button", name="Reconnect", exact=True)).to_be_enabled()
            await page.get_by_role("button", name="Reconnect", exact=True).click()
            await expect(
                page.get_by_role("button", name="Start speaking", exact=False)
            ).to_be_enabled(timeout=20000)
            assert offers[0] == offers[1]
            await expect(page.locator("audio")).to_be_visible()
            await page.wait_for_function(
                "document.querySelector('audio').currentTime > 0", timeout=10000
            )
            await page.get_by_role("button", name="Start speaking", exact=False).click()
            await asyncio.sleep(0.3)
            await page.get_by_role("button", name="Stop speaking", exact=False).click()
            await expect(page.locator(".score-display strong")).to_have_text("10", timeout=20000)
            await expect(page.get_by_role("log", name="Durable transcript")).to_contain_text(
                "I confirm my masters degree."
            )
            await expect(page.get_by_text("mock-dashboard", exact=True)).to_be_visible()
            # Scoring precedes agent-output persistence; reconnect cancels unfinished output.
            await expect(page.get_by_role("log", name="Durable transcript")).to_contain_text(
                "How many years", timeout=20000
            )
            screenshot = os.getenv("DASHBOARD_SCREENSHOT")
            if screenshot:
                await page.screenshot(path=screenshot, full_page=True)
            await page.get_by_role("button", name="Reconnect", exact=True).click()
            await expect(
                page.get_by_role("button", name="Start speaking", exact=False)
            ).to_be_enabled(timeout=20000)
            stt.script = MockSTTScript(text="Can I speak to a human?", interim=None)
            await page.get_by_role("button", name="Start speaking", exact=False).click()
            await asyncio.sleep(0.3)
            await page.get_by_role("button", name="Stop speaking", exact=False).click()
            await expect(page.get_by_role("log", name="Durable transcript")).to_contain_text(
                "Your request for human assistance", timeout=20000
            )
            await expect(page.get_by_role("button", name="Assign", exact=True)).to_be_enabled(
                timeout=20000
            )
            await expect(page.get_by_role("button", name="End call", exact=True)).to_have_count(
                0, timeout=20000
            )
            await page.get_by_role("button", name="Assign", exact=True).click()
            await page.get_by_role("button", name="Complete", exact=True).click()
            await expect(page.locator(".call-console .badge")).to_have_text("ended", timeout=10000)
            await page.reload()
            await expect(page.get_by_role("log", name="Durable transcript")).to_contain_text(
                "I confirm my masters degree."
            )
            assert errors == []
        finally:
            await browser.close()
    async with system.sessions() as db:
        current = await db.get(Conversation, system.conversation_id)
        call = await db.get(Call, system.call_id)
        assert current is not None and current.state == "COMPLETED"
        assert call is not None and call.status == "ENDED" and call.reconnect_attempts >= 1
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 5
        )


async def test_production_dashboard_lead_outage_preserves_durable_reads(
    system: System,
    dashboard: tuple[str, MockSTTProvider],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from playwright.async_api import async_playwright, expect
    from test_conversation_service import transition_to_greeting

    await transition_to_greeting(system)
    response = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns",
        headers={"X-Service-Token": "test-token"},
        json={
            "turn_id": str(uuid4()),
            "call_id": str(system.call_id),
            "expected_version": 3,
            "user_text": "I confirm my masters degree.",
            "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
        },
    )
    assert response.status_code == 200
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.getenv(
                "CHROME_EXECUTABLE", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ),
            headless=True,
        )
        try:
            page = await browser.new_page()
            await page.goto(dashboard[0])
            await expect(page.locator(".score-display strong")).to_have_text("10")
            original = system.conversation_app.state.lead_client.plan

            async def unavailable(*args: Any, **kwargs: Any) -> Any:
                raise LeadServiceUnavailableError("private error")

            monkeypatch.setattr(system.conversation_app.state.lead_client, "plan", unavailable)
            await expect(page.locator(".score-display strong")).to_have_text("—", timeout=10000)
            await expect(page.get_by_role("log", name="Durable transcript")).to_contain_text(
                "I confirm my masters degree."
            )
            await expect(page.locator(".call-console .badge")).to_have_text("connected")
            monkeypatch.setattr(system.conversation_app.state.lead_client, "plan", original)
            await expect(page.locator(".score-display strong")).to_have_text("10", timeout=10000)
        finally:
            await browser.close()


async def test_production_dashboard_recovers_lost_lead_creation_and_reload(
    system: System,
    dashboard: tuple[str, MockSTTProvider],
) -> None:
    from playwright.async_api import async_playwright, expect

    created_id: UUID | None = None
    posts = 0
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.getenv(
                "CHROME_EXECUTABLE", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ),
            headless=True,
        )
        try:
            page = await browser.new_page()

            async def lose_create(route: Any) -> None:
                nonlocal created_id, posts
                if route.request.method != "POST":
                    await route.continue_()
                    return
                posts += 1
                response = await route.fetch()
                assert response.status == 201
                created_id = UUID((await response.json())["id"])
                await route.fulfill(status=503, json={"detail": "simulated lost create reply"})

            await page.route("**/api/v1/leads", lose_create)
            await page.goto(dashboard[0])
            await page.get_by_role("button", name="Create synthetic lead", exact=False).click()
            await page.get_by_role("textbox", name="Name", exact=True).fill(
                "Dashboard Created Lead"
            )
            await page.get_by_role("textbox", name="Target country", exact=True).fill("Canada")
            await page.get_by_role("button", name="Create lead", exact=True).click()
            await expect(page.get_by_role("heading", name="Dashboard Created Lead")).to_be_visible()
            await expect(page).to_have_url(re.compile(f"lead={created_id}"))
            await page.reload()
            await expect(page.get_by_role("heading", name="Dashboard Created Lead")).to_be_visible()
            assert posts == 1
            async with system.sessions() as db:
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(Lead)
                        .where(Lead.display_name == "Dashboard Created Lead")
                    )
                    == 1
                )
        finally:
            await browser.close()
            if created_id is not None:
                async with system.sessions.begin() as db:
                    profiles = select(QualificationProfile.id).where(
                        QualificationProfile.lead_id == created_id
                    )
                    await db.execute(
                        delete(DomainEvent).where(
                            (DomainEvent.aggregate_id == created_id)
                            | DomainEvent.aggregate_id.in_(profiles)
                        )
                    )
                    await db.execute(delete(Lead).where(Lead.id == created_id))


async def test_dashboard_fault_reset_recovers_durable_turn_and_media(
    system: System,
    dashboard: tuple[str, MockSTTProvider],
) -> None:
    from playwright.async_api import async_playwright, expect

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.getenv(
                "CHROME_EXECUTABLE", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ),
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        try:
            page = await browser.new_page()
            await page.goto(dashboard[0])
            await page.get_by_role("button", name="Start / resume call", exact=False).click()
            await expect(
                page.get_by_role("button", name="Start speaking", exact=False)
            ).to_be_enabled(timeout=20000)
            await expect(page.get_by_role("button", name="Arm fault", exact=True)).to_be_enabled()
            await page.get_by_label("Demo fault").select_option("dependency:timeout")
            await page.get_by_role("button", name="Arm fault", exact=True).click()
            await expect(page.get_by_text("Armed: dependency", exact=False)).to_be_visible()
            await page.get_by_role("button", name="Start speaking", exact=False).click()
            await asyncio.sleep(0.3)
            await page.get_by_role("button", name="Stop speaking", exact=False).click()
            await expect(page.get_by_text("Voice operation:", exact=False)).to_be_visible(
                timeout=15000
            )
            await expect(page.get_by_role("log", name="Durable transcript")).to_contain_text(
                "I confirm my masters degree."
            )
            async with system.sessions() as db:
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(LeadScoreHistory)
                        .where(LeadScoreHistory.lead_id == system.lead_id)
                    )
                    == 0
                )
            await page.get_by_role("button", name="Reset fault", exact=True).click()
            await page.get_by_role("button", name="Recover operation", exact=True).click()
            await expect(page.locator(".score-display strong")).to_have_text("10", timeout=15000)
            await page.get_by_role("button", name="Disconnect media", exact=True).click()
            await page.get_by_role("button", name="Reconnect", exact=True).click()
            await expect(
                page.get_by_role("button", name="Start speaking", exact=False)
            ).to_be_enabled(timeout=20000)
            await page.get_by_text("Operational measurements", exact=True).click()
            await expect(page.get_by_text("conversation.apply", exact=False)).to_be_visible()
            await page.get_by_role("button", name="End call", exact=True).click()
        finally:
            await browser.close()
    async with system.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 2
        )  # greeting + recovered user; no audio/reply replay
        call = await db.get(Call, system.call_id)
        assert call is not None and call.status == "ENDED" and call.reconnect_attempts >= 1


async def test_two_dashboard_calls_keep_media_transcripts_and_scores_isolated(
    system: System,
    other_system: System,
    dashboard: tuple[str, MockSTTProvider],
) -> None:
    from playwright.async_api import async_playwright, expect

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.getenv(
                "CHROME_EXECUTABLE", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ),
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        try:
            pages = [await browser.new_page(), await browser.new_page()]
            origin = dashboard[0].split("?")[0]

            async def start(index: int, s: System) -> None:
                page = pages[index]
                await page.goto(f"{origin}?lead={s.lead_id}")
                await page.get_by_role("button", name="Start / resume call", exact=False).click()
                await expect(
                    page.get_by_role("button", name="Start speaking", exact=False)
                ).to_be_enabled(timeout=20000)
                await page.wait_for_function(
                    "document.querySelector('audio').currentTime > 0", timeout=10000
                )

            await asyncio.gather(start(0, system), start(1, other_system))
            await asyncio.gather(
                *(
                    page.get_by_role("button", name="Start speaking", exact=False).click()
                    for page in pages
                )
            )
            await asyncio.sleep(0.3)
            await asyncio.gather(
                *(
                    page.get_by_role("button", name="Stop speaking", exact=False).click()
                    for page in pages
                )
            )
            for page in pages:
                await expect(page.locator(".score-display strong")).to_have_text(
                    "10", timeout=20000
                )
                await expect(page.get_by_role("log", name="Durable transcript")).to_contain_text(
                    "How many years", timeout=20000
                )
            # Closing one peer/call must leave the other call usable.
            await pages[0].get_by_role("button", name="End call", exact=True).click()
            await expect(
                pages[1].get_by_role("button", name="Start speaking", exact=False)
            ).to_be_enabled()
            await pages[1].get_by_role("button", name="Reconnect", exact=True).click()
            await expect(
                pages[1].get_by_role("button", name="Start speaking", exact=False)
            ).to_be_enabled(timeout=20000)
            await pages[1].get_by_role("button", name="End call", exact=True).click()
        finally:
            await browser.close()
    for s in (system, other_system):
        async with s.sessions() as db:
            messages = list(
                await db.scalars(
                    select(Message)
                    .where(Message.conversation_id == s.conversation_id)
                    .order_by(Message.sequence_number)
                )
            )
            assert [m.speaker for m in messages] == ["AGENT", "USER", "AGENT"]
            assert all(m.call_id == s.call_id for m in messages)
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(LeadScoreHistory)
                    .where(LeadScoreHistory.lead_id == s.lead_id)
                )
                == 1
            )
            call = await db.get(Call, s.call_id)
            assert call is not None and call.status == "ENDED"
            assert call.reconnect_attempts == (0 if s is system else 1)
