import { expect, test, type Page } from "@playwright/test";

const leadId = "10000000-0000-4000-8000-000000000001";
const cid = "20000000-0000-4000-8000-000000000002";
const callId = "30000000-0000-4000-8000-000000000003";
const now = "2026-10-05T12:00:00Z";
const lead = {
  id: leadId,
  display_name: "Amelia Chen",
  target_country: "Canada",
  intent: "Skilled immigration",
  synthetic_profile_key: "amelia-chen",
  preferred_language: "en",
  status: "NEW",
  version: 1,
  created_at: now,
  updated_at: now,
};
const conversation = {
  id: cid,
  lead_id: leadId,
  state: "QUALIFICATION",
  version: 5,
  next_action: "ask_missing_qualification",
  created_at: now,
  completed_at: null,
  failure_reason: null,
};
const call = {
  id: callId,
  conversation_id: cid,
  status: "CONNECTED",
  version: 3,
  transport: "WEBRTC",
  reconnect_attempts: 0,
  created_at: now,
  failure_reason: null,
};
const qualification = {
  lead_id: leadId,
  version: 2,
  completeness: 16,
  status: "IN_PROGRESS",
  answers: [
    {
      field_key: "education_level",
      value: "masters",
      answer_status: "CONFIRMED",
      source: "USER_TRANSCRIPT",
    },
  ],
  score: {
    score: 10,
    classification: "COLD",
    rule_version: "baseline-v1",
    calculated_at: now,
    reasons: [],
  },
};
const messages = [
  {
    id: "u1",
    speaker: "USER",
    text: "I confirm my masters degree.",
    sequence_number: 1,
    turn_status: "APPLIED",
    created_at: now,
  },
  {
    id: "a1",
    speaker: "AGENT",
    text: "How many years of experience do you have?",
    sequence_number: 2,
    turn_status: "APPLIED",
    created_at: now,
    provider: "voice-runtime",
    model: "qualification-policy-v1",
  },
];

async function mockDashboard(
  page: Page,
  control: {
    outage?: boolean;
    calls?: number;
    lostWorkflow?: boolean;
    operations?: string[];
  } = {},
) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api", "");
    const respond = (json: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(json),
      });
    if (path === "/v1/leads")
      return respond({ items: [lead], total: 1, limit: 20, offset: 0 });
    if (path === `/v1/leads/${leadId}`) return respond(lead);
    if (path === `/v1/leads/${leadId}/qualification`)
      return respond(qualification);
    if (path === "/v1/conversations") {
      if (route.request().method() === "POST") {
        control.calls = (control.calls || 0) + 1;
        return respond({}, 409);
      }
      return respond({
        items: [conversation],
        total: 1,
        limit: 100,
        offset: 0,
      });
    }
    if (path === `/v1/conversations/${cid}`) return respond(conversation);
    if (path.endsWith("/calls"))
      return respond({
        items: [call],
        active_call: call,
        total: 1,
        limit: 100,
        offset: 0,
      });
    if (path.endsWith("/qualification-context"))
      return control.outage
        ? respond({}, 503)
        : respond({
            lead,
            plan: {
              qualification,
              next_question: "How many years of experience do you have?",
            },
          });
    if (path.endsWith("/history"))
      return respond({
        items: messages,
        has_more: false,
        next_before_sequence: null,
      });
    if (path.endsWith("/events"))
      return respond({
        items: [
          {
            event_id: "e1",
            event_type: "conversation.turn.applied",
            aggregate_type: "message",
            aggregate_version: 2,
            occurred_at: now,
            published_at: null,
            publish_attempts: 0,
          },
        ],
        total: 1,
        limit: 20,
        offset: 0,
      });
    if (path.endsWith("/workflows"))
      return respond({
        handoffs: [
          {
            id: "40000000-0000-4000-8000-000000000004",
            status: "REQUESTED",
            summary: "Caller requested human assistance.",
          },
        ],
        follow_ups: [],
      });
    if (path.endsWith("/transitions")) {
      const payload = route.request().postDataJSON() as {
        operation_id: string;
      };
      control.operations?.push(payload.operation_id);
      if (control.lostWorkflow) {
        control.lostWorkflow = false;
        return respond({}, 503);
      }
      return respond({ status: "ASSIGNED" });
    }
    return respond({}, 404);
  });
}

test("discovers existing conversation after reload and renders authoritative data", async ({
  page,
}) => {
  const control = { calls: 0 };
  await mockDashboard(page, control);
  await page.goto(`/?lead=${leadId}`);
  await expect(
    page.getByRole("heading", { name: "Amelia Chen" }),
  ).toBeVisible();
  await expect(page.locator(".score-display strong")).toHaveText("10");
  await expect(
    page.getByRole("log", { name: "Durable transcript" }),
  ).toContainText("I confirm my masters degree.");
  await expect(page.getByText("conversation.turn.applied")).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("combobox", { name: "Conversation history" }),
  ).toHaveValue(cid);
  expect(control.calls).toBe(0);
});

test("Lead outage removes current score while durable transcript and call state remain", async ({
  page,
}) => {
  const control = { outage: false };
  await mockDashboard(page, control);
  await page.goto(`/?lead=${leadId}`);
  await expect(page.locator(".score-display strong")).toHaveText("10");
  control.outage = true;
  await expect(page.locator(".score-display strong")).toHaveText("—", {
    timeout: 10000,
  });
  await expect(
    page.getByText("Qualification unavailable.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("log", { name: "Durable transcript" }),
  ).toContainText("I confirm my masters degree.");
  await expect(page.locator(".call-console .badge")).toHaveText("connected");
  control.outage = false;
  await expect(page.locator(".score-display strong")).toHaveText("10", {
    timeout: 10000,
  });
});

test("ambiguous workflow reply retries the same operation ID", async ({
  page,
}) => {
  const control = { lostWorkflow: true, operations: [] as string[] };
  await mockDashboard(page, control);
  await page.goto(`/?lead=${leadId}`);
  await page.getByRole("button", { name: "Assign", exact: true }).click();
  await page
    .getByRole("button", { name: "Recover same workflow operation" })
    .click();
  await expect(
    page.getByRole("button", { name: "Recover same workflow operation" }),
  ).toHaveCount(0);
  expect(control.operations.length).toBe(2);
  expect(new Set(control.operations).size).toBe(1);
});

test("microphone denial is explicit and does not create a replacement conversation", async ({
  page,
}) => {
  const control = { calls: 0 };
  await mockDashboard(page, control);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "mediaDevices", {
      value: {
        getUserMedia: async () => {
          throw new DOMException(
            "Microphone permission denied",
            "NotAllowedError",
          );
        },
      },
    });
  });
  await page.goto(`/?lead=${leadId}`);
  await page
    .getByRole("button", { name: "Start / resume call", exact: false })
    .click();
  await expect(page.locator(".alert[role=alert]")).toContainText(
    "Microphone permission denied",
  );
  expect(control.calls).toBe(0);
});

test("mobile layout stays usable and later-module controls are unavailable", async ({
  page,
}) => {
  await mockDashboard(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/?lead=${leadId}`);
  await expect(
    page.getByRole("heading", { name: "Amelia Chen" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Available in Module 16" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Available in Module 17" }),
  ).toBeDisabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("push-to-talk waits for backend readiness after WebRTC connects", async ({
  page,
}) => {
  await mockDashboard(page);
  let ready = false;
  await page.route("**/api/voice/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith("/offer")
      ? { sdp: "fixture", type: "answer", pc_id: "fixture-peer" }
      : path.endsWith("/status")
        ? {
            ready,
            pending_operation: false,
            closed: false,
            ending: false,
            providers: { llm: [], stt: [], tts: [] },
            media_state: "connected",
            speech_mode: "mock",
            llm_mode: "mock",
          }
        : {
            session_id: "fixture-session",
            token: "fixture-capability",
            speech_mode: "mock",
            llm_mode: "mock",
          };
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "mediaDevices", {
      value: {
        getUserMedia: async () => ({
          getTracks: () => [{ readyState: "live", stop() {} }],
        }),
      },
    });
    Object.defineProperty(window, "RTCPeerConnection", {
      value: class {
        connectionState = "connected";
        iceGatheringState = "complete";
        localDescription: unknown = null;
        onconnectionstatechange: (() => void) | null = null;
        addTrack() {}
        createDataChannel() {
          return { readyState: "open", send() {} };
        }
        async createOffer() {
          return { sdp: "fixture", type: "offer" };
        }
        async setLocalDescription(data: unknown) {
          this.localDescription = data;
        }
        async setRemoteDescription() {
          this.onconnectionstatechange?.();
        }
        close() {}
      },
    });
  });
  await page.goto(`/?lead=${leadId}`);
  await page
    .getByRole("button", { name: "Start / resume call", exact: false })
    .click();
  await expect(
    page.getByRole("button", { name: "Start speaking", exact: false }),
  ).toBeDisabled();
  ready = true;
  await expect(
    page.getByRole("button", { name: "Start speaking", exact: false }),
  ).toBeEnabled({ timeout: 10000 });
});

test("same-origin server proxy rejects unsupported routes and cross-origin mutations", async ({
  request,
}) => {
  const unknown = await request.get("/api/arbitrary-service");
  expect(unknown.status()).toBe(404);
  const crossOrigin = await request.post("/api/v1/leads", {
    headers: { Origin: "https://unrelated.example" },
    data: {},
  });
  expect(crossOrigin.status()).toBe(403);
  const unavailable = await request.get("/api/health");
  expect(unavailable.status()).toBe(503);
  expect(await unavailable.text()).not.toContain("127.0.0.1:9");
});
