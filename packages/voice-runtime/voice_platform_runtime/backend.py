"""Typed, bounded internal Conversation HTTP boundary; no database access."""

import asyncio
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError
from voice_platform_contracts.conversation import (
    AgentMessageCreate,
    CallResponse,
    CallTransition,
    ConversationLiveState,
    ConversationResponse,
    ConversationTransition,
    ConversationTurn,
    ConversationTurnResponse,
    MessageHistoryEntry,
    MessageHistoryResponse,
)
from voice_platform_contracts.qualification import (
    ProposedFacts,
    QualificationContext,
    StagedTurnCreate,
)
from voice_platform_contracts.workflow import (
    AgentOutputCheck,
    AgentOutputDecision,
    WorkflowActionCreate,
    WorkflowActionResponse,
)


class DependencyError(RuntimeError):
    def __init__(self, status: int = 503) -> None:
        super().__init__("conversation operation unavailable")
        self.status = status

    @property
    def code(self) -> str:
        return {
            404: "operation_not_found",
            409: "operation_conflict",
            422: "operation_rejected",
        }.get(self.status, "dependency_unavailable")


class Backend:
    def __init__(self, client: httpx.AsyncClient, token: str | None, timeout: float = 5) -> None:
        self.client = client
        self.token = token
        self.timeout = timeout

    async def request[T: BaseModel](
        self,
        method: str,
        path: str,
        model: type[T],
        request_id: UUID,
        payload: BaseModel | None = None,
    ) -> T:
        headers = {"X-Request-ID": str(request_id)}
        if self.token:
            headers["X-Service-Token"] = self.token
        try:
            async with asyncio.timeout(self.timeout):
                response = await self.client.request(
                    method,
                    path,
                    headers=headers,
                    json=payload.model_dump(mode="json") if payload is not None else None,
                )
            if response.status_code != 200:
                raise DependencyError(
                    response.status_code if response.status_code in {404, 409, 422} else 503
                )
            return model.model_validate(response.json())
        except (httpx.RequestError, TimeoutError, ValueError, ValidationError):
            raise DependencyError() from None

    async def conversation(self, cid: UUID, rid: UUID) -> ConversationResponse:
        result = await self.request("GET", f"/v1/conversations/{cid}", ConversationResponse, rid)
        if result.id != cid:
            raise DependencyError()
        return result

    async def live(self, cid: UUID, rid: UUID) -> ConversationLiveState:
        result = await self.request(
            "GET", f"/v1/conversations/{cid}/live-state", ConversationLiveState, rid
        )
        if (
            result.conversation.id != cid
            or (result.active_call is not None and result.active_call.conversation_id != cid)
            or result.qualification.lead_id != result.conversation.lead_id
        ):
            raise DependencyError()
        return result

    async def history(self, cid: UUID, rid: UUID) -> MessageHistoryResponse:
        result = await self.request(
            "GET", f"/v1/conversations/{cid}/history?limit=100", MessageHistoryResponse, rid
        )
        if any(item.conversation_id != cid for item in result.items):
            raise DependencyError()
        return result

    async def state(
        self, cid: UUID, current: ConversationResponse, target: str, rid: UUID
    ) -> ConversationResponse:
        payload = ConversationTransition.model_validate(
            {"target_state": target, "expected_version": current.version}
        )
        result = await self.request(
            "POST", f"/v1/conversations/{cid}/transitions", ConversationResponse, rid, payload
        )
        if result.id != cid or result.state != target:
            raise DependencyError()
        return result

    async def call(
        self, cid: UUID, current: CallResponse, target: str, rid: UUID, reason: str | None = None
    ) -> CallResponse:
        payload = CallTransition.model_validate(
            {"target_status": target, "expected_version": current.version, "reason": reason}
        )
        result = await self.request(
            "POST",
            f"/v1/conversations/{cid}/calls/{current.id}/transitions",
            CallResponse,
            rid,
            payload,
        )
        if result.id != current.id or result.conversation_id != cid or result.status != target:
            raise DependencyError()
        return result

    async def user(
        self, cid: UUID, payload: ConversationTurn, rid: UUID
    ) -> ConversationTurnResponse:
        result = await self.request(
            "POST", f"/v1/conversations/{cid}/turns", ConversationTurnResponse, rid, payload
        )
        if (
            result.conversation.id != cid
            or result.call.id != payload.call_id
            or result.message_id != payload.turn_id
            or result.turn_status != "APPLIED"
        ):
            raise DependencyError()
        return result

    async def agent(self, cid: UUID, payload: AgentMessageCreate, rid: UUID) -> MessageHistoryEntry:
        result = await self.request(
            "POST", f"/v1/conversations/{cid}/agent-messages", MessageHistoryEntry, rid, payload
        )
        if (
            result.conversation_id != cid
            or result.call_id != payload.call_id
            or result.id != payload.message_id
            or result.speaker != "AGENT"
            or result.redacted
            or result.text != payload.text
            or result.provider != payload.provider
            or result.model != payload.model
        ):
            raise DependencyError()
        return result

    async def qualification_context(self, cid: UUID, rid: UUID) -> QualificationContext:
        result = await self.request(
            "GET", f"/v1/conversations/{cid}/qualification-context", QualificationContext, rid
        )
        current = await self.conversation(cid, rid)
        if (
            result.lead.id != current.lead_id
            or result.plan.qualification.lead_id != current.lead_id
        ):
            raise DependencyError()
        return result

    async def stage(self, cid: UUID, payload: StagedTurnCreate, rid: UUID) -> MessageHistoryEntry:
        result = await self.request(
            "POST", f"/v1/conversations/{cid}/staged-turns", MessageHistoryEntry, rid, payload
        )
        if (
            result.id != payload.turn_id
            or result.conversation_id != cid
            or result.call_id != payload.call_id
            or result.speaker != "USER"
            or result.redacted
        ):
            raise DependencyError()
        return result

    async def bind(
        self, cid: UUID, tid: UUID, payload: ProposedFacts, rid: UUID
    ) -> MessageHistoryEntry:
        result = await self.request(
            "POST",
            f"/v1/conversations/{cid}/staged-turns/{tid}/facts",
            MessageHistoryEntry,
            rid,
            payload,
        )
        if result.id != tid or result.conversation_id != cid or result.speaker != "USER":
            raise DependencyError()
        return result

    async def apply(self, cid: UUID, tid: UUID, rid: UUID) -> ConversationTurnResponse:
        result = await self.request(
            "POST",
            f"/v1/conversations/{cid}/staged-turns/{tid}/apply",
            ConversationTurnResponse,
            rid,
        )
        if (
            result.message_id != tid
            or result.conversation.id != cid
            or result.turn_status != "APPLIED"
            or result.qualification is None
            or result.qualification.lead_id != result.conversation.lead_id
        ):
            raise DependencyError()
        return result

    async def workflow(
        self, cid: UUID, data: WorkflowActionCreate, rid: UUID
    ) -> WorkflowActionResponse:
        result = await self.request(
            "POST", f"/v1/conversations/{cid}/workflow-actions", WorkflowActionResponse, rid, data
        )
        if (
            result.action_id != data.action_id
            or result.action != data.action
            or result.conversation.id != cid
            or result.acknowledgement.conversation_id != cid
            or result.acknowledgement.call_id != data.call_id
            or result.acknowledgement.speaker != "AGENT"
            or result.acknowledgement.turn_status != "APPLIED"
        ):
            raise DependencyError()
        return result

    async def output_policy(
        self, cid: UUID, data: AgentOutputCheck, rid: UUID
    ) -> AgentOutputDecision:
        return await self.request(
            "POST", f"/v1/conversations/{cid}/output-policy", AgentOutputDecision, rid, data
        )

    async def workflow_result(
        self, cid: UUID, action_id: UUID, rid: UUID
    ) -> WorkflowActionResponse:
        result = await self.request(
            "GET",
            f"/v1/conversations/{cid}/workflow-actions/{action_id}",
            WorkflowActionResponse,
            rid,
        )
        if (
            result.action_id != action_id
            or result.conversation.id != cid
            or result.acknowledgement.conversation_id != cid
        ):
            raise DependencyError()
        return result
