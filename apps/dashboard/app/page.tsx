"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  conversational,
  dateTime,
  label,
  terminal,
} from "../lib/api";
import type {
  Call,
  Calls,
  Context,
  Conversation,
  DomainEvent,
  History,
  Lead,
  Message,
  Page,
  Qualification,
  SessionStatus,
  Workflow,
  Workflows,
  OperationalOverview,
} from "../lib/types";
import { VoiceClient, type VoiceEvent } from "../lib/voice";

const fields = [
  "education_level",
  "years_experience",
  "english_level",
  "has_job_offer",
  "budget_ready",
  "urgency",
];
const emptyWorkflows: Workflows = { handoffs: [], follow_ups: [] };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const errorMessage = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Operation unavailable. Please retry.";
const valueLabel = (value: unknown) =>
  value === true
    ? "Yes"
    : value === false
      ? "No"
      : typeof value === "string" || typeof value === "number"
        ? String(value)
        : "Unknown";

function Badge({ value }: { value: string }) {
  const tone = [
    "CONNECTED",
    "CONFIRMED",
    "HEALTHY",
    "HOT",
    "COMPLETED",
    "READY",
  ].includes(value)
    ? "green"
    : ["FAILED", "CONTRADICTORY", "UNAVAILABLE", "OPEN"].includes(value)
      ? "red"
      : [
            "PENDING",
            "PROVISIONAL",
            "RECONNECTING",
            "WARM",
            "DEGRADED",
            "HALF_OPEN",
          ].includes(value)
        ? "amber"
        : "neutral";
  return <span className={`badge ${tone}`}>{label(value)}</span>;
}

export default function Dashboard() {
  const [leads, setLeads] = useState<Page<Lead> | null>(null);
  const [leadOffset, setLeadOffset] = useState(0);
  const [leadFilter, setLeadFilter] = useState("");
  const [leadId, setLeadId] = useState<string | null>(null);
  const [lead, setLead] = useState<Lead | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [cid, setCid] = useState<string | null>(null);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [calls, setCalls] = useState<Calls | null>(null);
  const [qualification, setQualification] = useState<Qualification | null>(
    null,
  );
  const [question, setQuestion] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [historyMore, setHistoryMore] = useState<number | null>(null);
  const [workflows, setWorkflows] = useState<Workflows>(emptyWorkflows);
  const [events, setEvents] = useState<DomainEvent[]>([]);
  const [eventOffset, setEventOffset] = useState(0);
  const [eventTotal, setEventTotal] = useState(0);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [manual, setManual] = useState(true);
  const [speaking, setSpeaking] = useState(false);
  const [agentReady, setAgentReady] = useState(false);
  const [media, setMedia] = useState("idle");
  const [hasSession, setHasSession] = useState(false);
  const [sessionStatus, setSessionStatus] = useState<SessionStatus | null>(
    null,
  );
  const [partial, setPartial] = useState<string | null>(null);
  const [retryRequired, setRetryRequired] = useState(false);
  const [activity, setActivity] = useState<
    { type: string; text: string; at: string }[]
  >([]);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newCountry, setNewCountry] = useState("");
  const [newIntent, setNewIntent] = useState("");
  const [updated, setUpdated] = useState<string | null>(null);
  const [operations, setOperations] = useState<OperationalOverview | null>(
    null,
  );
  const [faultChoice, setFaultChoice] = useState("llm:unavailable");
  const voice = useRef<VoiceClient | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const selection = useRef({
    leadId: null as string | null,
    cid: null as string | null,
  });
  const refreshing = useRef(false);
  const refreshAgain = useRef(false);
  const eventPage = useRef(0);
  const pendingLead = useRef<{
    display_name: string;
    synthetic_profile_key: string;
    target_country: string | null;
    intent: string | null;
  } | null>(null);
  const pendingWorkflow = useRef<{
    cid: string;
    path: string;
    payload: {
      operation_id: string;
      expected_status: string;
      target_status: string;
    };
  } | null>(null);
  const operationLock = useRef(false);
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const mounted = useRef(true);

  const setError = useCallback((key: string, error?: unknown) => {
    setErrors((previous) => {
      const next = { ...previous };
      if (error) next[key] = errorMessage(error);
      else delete next[key];
      return next;
    });
  }, []);

  const loadLeads = useCallback(async () => {
    try {
      const result = await api<Page<Lead>>(
        `/v1/leads?limit=20&offset=${leadOffset}`,
      );
      if (mounted.current) {
        setLeads(result);
        setError("leads");
      }
    } catch (error) {
      if (mounted.current) setError("leads", error);
    }
  }, [leadOffset, setError]);

  useEffect(() => {
    mounted.current = true;
    const params = new URLSearchParams(window.location.search);
    const requested = params.get("lead");
    if (requested && uuid.test(requested)) setLeadId(requested);
    const leave = () => voice.current?.dispose();
    window.addEventListener("pagehide", leave);
    return () => {
      mounted.current = false;
      window.removeEventListener("pagehide", leave);
      voice.current?.dispose();
    };
  }, []);
  useEffect(() => {
    void loadLeads();
  }, [loadLeads]);

  useEffect(() => {
    selection.current = { leadId, cid: null };
    setLead(null);
    setCid(null);
    setConversations([]);
    setQualification(null);
    setQuestion(null);
    setErrors((previous) => ({
      ...(previous.leads ? { leads: previous.leads } : {}),
    }));
    if (!leadId) return;
    const selectedUrl = new URLSearchParams(window.location.search);
    if (selectedUrl.get("lead") !== leadId) {
      window.history.replaceState(
        null,
        "",
        "?" + new URLSearchParams({ lead: leadId }),
      );
    }
    let cancelled = false;
    api<Lead>(`/v1/leads/${leadId}`)
      .then((result) => {
        if (!cancelled) setLead(result);
      })
      .catch((error) => {
        if (!cancelled) setError("profile", error);
      });
    const discover = async () => {
      try {
        const [all, active] = await Promise.all([
          api<Page<Conversation>>(
            `/v1/conversations?lead_id=${leadId}&limit=100`,
          ),
          api<Page<Conversation>>(
            `/v1/conversations?lead_id=${leadId}&active_only=true&limit=1`,
          ),
        ]);
        if (cancelled) return;
        const combined = [
          ...active.items,
          ...all.items.filter(
            (item) => !active.items.some((a) => a.id === item.id),
          ),
        ];
        setConversations(combined);
        const requested = new URLSearchParams(window.location.search).get(
          "conversation",
        );
        setCid(
          combined.some((item) => item.id === requested)
            ? requested
            : active.items[0]?.id || all.items[0]?.id || null,
        );
        setError("discovery");
      } catch (error) {
        if (!cancelled) setError("discovery", error);
      }
    };
    void discover();
    return () => {
      cancelled = true;
    };
  }, [leadId, setError]);

  const refresh = useCallback(async () => {
    const target = selection.current.cid;
    if (!target) return;
    if (refreshing.current) {
      refreshAgain.current = true;
      return;
    }
    refreshing.current = true;
    const current = () => mounted.current && selection.current.cid === target;
    const read = async <T,>(
      key: string,
      path: string,
      apply: (data: T) => void,
      clear?: () => void,
    ) => {
      try {
        const result = await api<T>(path);
        if (current()) {
          apply(result);
          setError(key);
        }
      } catch (error) {
        if (current()) {
          clear?.();
          setError(key, error);
        }
      }
    };
    try {
      await Promise.all([
        read<OperationalOverview>(
          "observability",
          "/v1/observability",
          setOperations,
          () => setOperations(null),
        ),
        read<Conversation>(
          "conversation",
          `/v1/conversations/${target}`,
          (result) => {
            setConversation(result);
            setConversations((previous) =>
              previous.map((item) => (item.id === result.id ? result : item)),
            );
          },
        ),
        read<Calls>(
          "calls",
          `/v1/conversations/${target}/calls?limit=100`,
          setCalls,
        ),
        read<History>(
          "history",
          `/v1/conversations/${target}/history?limit=50`,
          (result) => {
            setMessages((previous) => {
              const first = result.items[0]?.sequence_number;
              return [
                ...previous.filter(
                  (item) => first !== undefined && item.sequence_number < first,
                ),
                ...result.items,
              ].slice(-500);
            });
            setHistoryMore((previous) =>
              previous === null
                ? result.next_before_sequence
                : Math.min(previous, result.next_before_sequence ?? previous),
            );
            setPartial((previous) =>
              result.items.some(
                (item) => item.speaker === "USER" && item.text === previous,
              )
                ? null
                : previous,
            );
          },
        ),
        read<Context>(
          "qualification",
          `/v1/conversations/${target}/qualification-context`,
          (result) => {
            setQualification(result.plan.qualification);
            setQuestion(result.plan.next_question);
            setLead(result.lead);
          },
          () => {
            setQualification(null);
            setQuestion(null);
          },
        ),
        read<Workflows>(
          "workflows",
          `/v1/conversations/${target}/workflows`,
          setWorkflows,
        ),
        read<Page<DomainEvent>>(
          "events",
          `/v1/conversations/${target}/events?limit=20&offset=${eventPage.current}`,
          (result) => {
            setEvents(result.items);
            setEventTotal(result.total);
          },
        ),
      ]);
      const client = voice.current;
      if (client?.session) {
        try {
          const status = await client.status();
          if (current()) {
            setSessionStatus(status);
            setAgentReady(
              status.ready && client.channel?.readyState === "open",
            );
            setError("providers");
            setRetryRequired(status.pending_operation);
            if (status.closed) {
              await client.end();
              setHasSession(false);
              setMedia("ended");
              setAgentReady(false);
              setSessionStatus(null);
            }
          }
        } catch (error) {
          if (current()) {
            setSessionStatus(null);
            setError("providers", error);
          }
        }
      }
      if (current()) setUpdated(new Date().toISOString());
    } finally {
      refreshing.current = false;
      if (refreshAgain.current) {
        refreshAgain.current = false;
        void refreshRef.current();
      }
    }
  }, [setError]);
  refreshRef.current = refresh;

  useEffect(() => {
    selection.current = { leadId, cid };
    setConversation(null);
    setErrors((previous) =>
      Object.fromEntries(
        Object.entries(previous).filter(([key]) =>
          ["leads", "profile", "discovery"].includes(key),
        ),
      ),
    );
    setCalls(null);
    setMessages([]);
    setHistoryMore(null);
    setQualification(null);
    setQuestion(null);
    setWorkflows(emptyWorkflows);
    setEvents([]);
    setEventOffset(0);
    eventPage.current = 0;
    setPartial(null);
    setUpdated(null);
    if (!cid) return;
    const params = new URLSearchParams({ lead: leadId!, conversation: cid });
    window.history.replaceState(null, "", "?" + params);
    void refresh();
    const interval = setInterval(() => {
      void refresh();
    }, 2500);
    return () => clearInterval(interval);
  }, [cid, leadId, refresh]);

  // A lead with no conversation still reads its authoritative qualification.
  useEffect(() => {
    if (!leadId || cid || errors.discovery) return;
    let cancelled = false;
    api<Qualification>(`/v1/leads/${leadId}/qualification`)
      .then((result) => {
        if (!cancelled) {
          setQualification(result);
          setError("qualification");
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setQualification(null);
          setError("qualification", error);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [leadId, cid, errors.discovery, setError]);

  const notify = useCallback(
    (event: VoiceEvent) => {
      if (!mounted.current) return;
      if (event.type === "media") {
        setMedia(String(event.state));
        if (event.state !== "connected") setAgentReady(false);
        setSpeaking(false);
      }
      if (event.type === "transcript" && typeof event.text === "string")
        setPartial(event.text.slice(0, 20000));
      if (event.type === "connected") {
        setMedia("connected");
        setAgentReady(true);
      }
      if (event.type === "error") {
        setAgentReady(false);
        setRetryRequired(Boolean(event.retry_required));
        setError(
          "voice",
          new Error(
            `Voice operation: ${String(event.code)}. Use recovery after the dependency returns.`,
          ),
        );
      }
      if (event.type === "ready" || event.type === "recovered") {
        setAgentReady(true);
        setError("voice");
        setRetryRequired(false);
      }
      if (event.type === "playback_blocked")
        setNotice("Press play on the audio controls to enable sound.");
      const text =
        event.type === "session"
          ? `Speech ${event.speech_mode} · LLM ${event.llm_mode}`
          : event.type === "error"
            ? String(event.code)
            : event.type === "media"
              ? String(event.state)
              : event.type === "workflow"
                ? "Workflow committed; media will close separately"
                : event.type === "qualification"
                  ? "Authoritative qualification applied"
                  : label(event.type);
      setActivity((previous) =>
        [
          ...previous,
          { type: event.type, text, at: new Date().toISOString() },
        ].slice(-40),
      );
      if (
        [
          "qualification",
          "workflow",
          "agent",
          "recovered",
          "connected",
        ].includes(event.type)
      )
        void refreshRef.current();
    },
    [setError],
  );

  async function action(name: string, operation: () => Promise<void>) {
    if (operationLock.current) return;
    operationLock.current = true;
    setBusy(name);
    setNotice(null);
    setError("operation");
    try {
      await operation();
    } catch (error) {
      setError("operation", error);
    } finally {
      operationLock.current = false;
      if (mounted.current) {
        setBusy(null);
        setHasSession(Boolean(voice.current?.session));
        void refresh();
      }
    }
  }

  async function startCall() {
    if (!leadId || !audio.current) return;
    if (voice.current?.session)
      throw new Error(
        "End or recover the existing media session before starting another.",
      );
    // Reconcile discovery after ambiguous create replies instead of blindly repeating writes.
    let active = (
      await api<Page<Conversation>>(
        `/v1/conversations?lead_id=${leadId}&active_only=true&limit=1`,
      )
    ).items[0];
    if (!active) {
      try {
        active = await api<Conversation>("/v1/conversations", "POST", {
          lead_id: leadId,
        });
      } catch (error) {
        const found = await api<Page<Conversation>>(
          `/v1/conversations?lead_id=${leadId}&active_only=true&limit=1`,
        );
        if (!found.items[0]) throw error;
        active = found.items[0];
      }
    }
    setCid(active.id);
    selection.current.cid = active.id;
    setConversations((previous) => [
      active,
      ...previous.filter((c) => c.id !== active.id),
    ]);
    if (!conversational(active.state))
      throw new Error(
        "Finish the outstanding workflow before starting another qualification call.",
      );
    let call = (await api<Calls>(`/v1/conversations/${active.id}/calls`))
      .active_call;
    if (!call) {
      try {
        call = await api<Call>(`/v1/conversations/${active.id}/calls`, "POST", {
          transport: "WEBRTC",
        });
      } catch (error) {
        const found = await api<Calls>(`/v1/conversations/${active.id}/calls`);
        if (!found.active_call) throw error;
        call = found.active_call;
      }
    }
    const client = new VoiceClient(notify, audio.current);
    voice.current = client;
    setAgentReady(false);
    setMedia("connecting");
    try {
      await client.connect(active.id, call.id, manual);
      setError("voice");
    } catch (error) {
      setMedia("connection failed");
      throw error;
    }
  }

  async function createLead() {
    if (!pendingLead.current)
      pendingLead.current = {
        display_name: newName.trim(),
        synthetic_profile_key: "dashboard-" + crypto.randomUUID(),
        target_country: newCountry.trim() || null,
        intent: newIntent.trim() || null,
      };
    let created: Lead;
    try {
      created = await api<Lead>("/v1/leads", "POST", pendingLead.current);
    } catch (error) {
      // Synthetic key uniqueness makes the same create identity recoverable without duplicates.
      if (error instanceof ApiError && error.status === 422) {
        pendingLead.current = null;
        throw error;
      }
      if (!(error instanceof ApiError) || ![409, 503].includes(error.status))
        throw error;
      let found: Lead | undefined;
      const deadline = performance.now() + 15000;
      for (
        let offset = 0;
        offset < 1000 && performance.now() < deadline;
        offset += 100
      ) {
        const page = await api<Page<Lead>>(
          `/v1/leads?limit=100&offset=${offset}`,
        );
        found = page.items.find(
          (item) =>
            item.synthetic_profile_key ===
            pendingLead.current!.synthetic_profile_key,
        );
        if (found || offset + 100 >= page.total) break;
      }
      if (!found) throw error;
      created = found;
    }
    pendingLead.current = null;
    setLeadId(created.id);
    setShowCreate(false);
    setNewName("");
    setNewCountry("");
    setNewIntent("");
    await loadLeads();
  }

  async function transitionWorkflow(
    kind: "handoffs" | "follow-ups",
    row: Workflow,
    target: string,
  ) {
    if (!cid) return;
    const path = `/v1/conversations/${cid}/${kind}/${row.id}/transitions`;
    if (pendingWorkflow.current && pendingWorkflow.current.path !== path)
      throw new Error("Recover the pending workflow operation first.");
    if (!pendingWorkflow.current)
      pendingWorkflow.current = {
        cid,
        path,
        payload: {
          operation_id: crypto.randomUUID(),
          expected_status: row.status,
          target_status: target,
        },
      };
    try {
      await api(path, "POST", pendingWorkflow.current.payload);
      pendingWorkflow.current = null;
    } catch (error) {
      if (error instanceof ApiError && [404, 409, 422].includes(error.status))
        pendingWorkflow.current = null;
      throw error;
    }
  }

  async function olderHistory() {
    if (!cid || !historyMore) return;
    const target = cid;
    const result = await api<History>(
      `/v1/conversations/${cid}/history?limit=50&before_sequence=${historyMore}`,
    );
    if (selection.current.cid !== target) return;
    setMessages((previous) =>
      [
        ...result.items,
        ...previous.filter(
          (item) => !result.items.some((old) => old.id === item.id),
        ),
      ].slice(-500),
    );
    setHistoryMore(result.next_before_sequence);
  }

  const activeCall = calls?.active_call;
  const visibleLeads =
    leads?.items.filter((item) =>
      `${item.display_name} ${item.target_country || ""}`
        .toLowerCase()
        .includes(leadFilter.toLowerCase()),
    ) || [];
  const statusLabel = qualification?.score?.classification || "UNSCORED";
  const locked = !!busy || hasSession || pendingWorkflow.current !== null;

  function workflowRows(kind: "handoffs" | "follow-ups", rows: Workflow[]) {
    return rows.map((row) => (
      <div className="workflow-row" key={row.id}>
        <div>
          <Badge value={row.status} />
          <p>
            {row.summary ||
              (row.scheduled_at
                ? `Reminder · ${dateTime(row.scheduled_at)}`
                : label(row.reason || "follow up"))}
          </p>
          {row.attempt !== undefined && (
            <small>
              Attempt {row.attempt} of {row.max_attempts}
              {row.last_error ? ` · ${label(row.last_error)}` : ""}
            </small>
          )}
        </div>
        <div className="workflow-actions">
          {kind === "handoffs" && row.status === "REQUESTED" && (
            <button
              disabled={!!busy}
              onClick={() =>
                void action("Assigning", () =>
                  transitionWorkflow(kind, row, "ASSIGNED"),
                )
              }
            >
              Assign
            </button>
          )}
          {((kind === "handoffs" && row.status === "ASSIGNED") ||
            row.status === "READY") && (
            <button
              disabled={!!busy}
              onClick={() =>
                void action("Completing", () =>
                  transitionWorkflow(kind, row, "COMPLETED"),
                )
              }
            >
              Complete
            </button>
          )}
          {row.status === "FAILED" && kind === "follow-ups" && (
            <button
              disabled={!!busy}
              onClick={() =>
                void action("Retrying", () =>
                  transitionWorkflow(kind, row, "SCHEDULED"),
                )
              }
            >
              Retry dispatch
            </button>
          )}
          {["REQUESTED", "ASSIGNED", "SCHEDULED", "READY", "FAILED"].includes(
            row.status,
          ) && (
            <button
              disabled={!!busy}
              onClick={() =>
                void action("Cancelling", () =>
                  transitionWorkflow(kind, row, "CANCELLED"),
                )
              }
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    ));
  }

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Vox home">
          <span className="brand-mark">◖</span>vox
          <span className="brand-divider">/</span>
          <span className="brand-caption">Lead operations</span>
        </a>
        <div className="topbar-right">
          <span className="environment">
            <span className="dot" /> LOCAL DEMO
          </span>
          <span className="avatar">OP</span>
        </div>
      </header>
      <main>
        <div className="page-heading">
          <div>
            <p className="eyebrow">OPERATIONS CONSOLE</p>
            <h1>Every conversation, in context.</h1>
            <p className="subheading">
              Qualify in real time. Keep the next step clear.
            </p>
          </div>
          <button
            className="primary"
            disabled={locked}
            onClick={() => setShowCreate(true)}
          >
            ＋ Create synthetic lead
          </button>
        </div>
        {errors.operation && (
          <div className="alert" role="alert">
            {errors.operation}
          </div>
        )}
        {notice && (
          <div className="info" role="status">
            {notice}
          </div>
        )}
        {showCreate && (
          <form
            className="create-form card"
            onSubmit={(event) => {
              event.preventDefault();
              void action("Creating lead", createLead);
            }}
          >
            <div>
              <h2>New synthetic lead</h2>
              <p>Demo data stored in Lead Service.</p>
            </div>
            <label>
              Name
              <input
                required
                maxLength={160}
                value={newName}
                disabled={!!busy || !!pendingLead.current}
                onChange={(e) => setNewName(e.target.value)}
              />
            </label>
            <label>
              Target country
              <input
                maxLength={80}
                value={newCountry}
                disabled={!!busy || !!pendingLead.current}
                onChange={(e) => setNewCountry(e.target.value)}
              />
            </label>
            <label>
              Intent
              <input
                maxLength={160}
                value={newIntent}
                disabled={!!busy || !!pendingLead.current}
                onChange={(e) => setNewIntent(e.target.value)}
              />
            </label>
            <button className="primary" disabled={!!busy}>
              {pendingLead.current ? "Retry same creation" : "Create lead"}
            </button>
            <button
              type="button"
              disabled={!!busy || !!pendingLead.current}
              onClick={() => setShowCreate(false)}
            >
              Close
            </button>
          </form>
        )}
        <div className="workspace">
          <aside className="lead-panel card">
            <div className="panel-heading">
              <h2>
                Leads <span className="count">{leads?.total ?? "—"}</span>
              </h2>
              <button
                className="icon-button"
                aria-label="Refresh leads"
                onClick={() => void loadLeads()}
              >
                ↻
              </button>
            </div>
            <label className="search-label">
              <span>⌕</span>
              <input
                aria-label="Search this lead page"
                placeholder="Search this page…"
                value={leadFilter}
                onChange={(e) => setLeadFilter(e.target.value)}
              />
            </label>
            {errors.leads && (
              <div className="inline-error" role="alert">
                {errors.leads}
              </div>
            )}
            {!leads && !errors.leads && (
              <p className="empty">Loading synthetic leads…</p>
            )}
            {leads && !visibleLeads.length && (
              <p className="empty">
                No leads on this page. Create a synthetic lead to begin.
              </p>
            )}
            <div className="lead-list">
              {visibleLeads.map((item) => (
                <button
                  key={item.id}
                  className={`lead-item ${leadId === item.id ? "selected" : ""}`}
                  disabled={locked}
                  onClick={() => {
                    setLeadId(item.id);
                    pendingWorkflow.current = null;
                  }}
                >
                  <span className="initials">
                    {item.display_name
                      .split(" ")
                      .slice(0, 2)
                      .map((word) => word[0])
                      .join("")}
                  </span>
                  <span>
                    <strong>{item.display_name}</strong>
                    <small>
                      {item.target_country || "Country not provided"}
                    </small>
                  </span>
                  <span className="lead-status">{label(item.status)}</span>
                </button>
              ))}
            </div>
            {leads && (
              <div className="pager">
                <button
                  disabled={locked || leadOffset === 0}
                  onClick={() => setLeadOffset(Math.max(0, leadOffset - 20))}
                >
                  ←
                </button>
                <small>
                  {leads.total
                    ? `${leadOffset + 1}–${Math.min(leadOffset + 20, leads.total)} of ${leads.total}`
                    : "0 leads"}
                </small>
                <button
                  disabled={locked || leadOffset + 20 >= leads.total}
                  onClick={() => setLeadOffset(leadOffset + 20)}
                >
                  →
                </button>
              </div>
            )}
            <div className="sidebar-note">
              <span>◇</span>
              <p>
                Synthetic leads.
                <br />
                Real backend state.
              </p>
            </div>
          </aside>
          {!leadId ? (
            <section className="welcome card">
              <div className="orb small-orb">◖</div>
              <p className="eyebrow">READY WHEN YOU ARE</p>
              <h2>Select a lead to begin.</h2>
              <p>
                Your live call, qualification and next steps will appear here.
              </p>
            </section>
          ) : (
            <div className="lead-workspace">
              <section className="profile-strip card">
                <div>
                  <p className="eyebrow">SELECTED LEAD</p>
                  <h2>{lead?.display_name || "Loading lead…"}</h2>
                  <p>
                    {lead?.intent || "Intent not provided"} <span>·</span>{" "}
                    {lead?.target_country || "Country not provided"}
                  </p>
                </div>
                <div className="profile-meta">
                  <Badge value={lead?.status || "UNKNOWN"} />
                  <small>
                    {lead?.preferred_language.toUpperCase()} · Synthetic profile
                  </small>
                </div>
              </section>
              {(errors.discovery || errors.profile) && (
                <div className="inline-error" role="alert">
                  {errors.discovery || errors.profile}
                  <button
                    disabled={locked}
                    onClick={() =>
                      void action("Refreshing", async () => {
                        const id = leadId;
                        setLeadId(null);
                        await new Promise((resolve) => setTimeout(resolve, 0));
                        setLeadId(id);
                      })
                    }
                  >
                    Retry discovery
                  </button>
                </div>
              )}
              <div className="detail-grid">
                <div className="main-column">
                  <section className="call-console card">
                    <div className="panel-heading">
                      <h2>
                        <span className="dot" /> Live call
                      </h2>
                      <Badge
                        value={
                          activeCall?.status ||
                          calls?.items[0]?.status ||
                          "NO_CALL"
                        }
                      />
                    </div>
                    <div
                      className={`voice-stage ${media === "connected" ? "connected" : ""}`}
                    >
                      <div className="orb">
                        <div className="wave">
                          <i />
                          <i />
                          <i />
                          <i />
                          <i />
                        </div>
                      </div>
                      <h3>
                        {media === "connected"
                          ? speaking
                            ? "Listening to you"
                            : "Conversation in progress"
                          : hasSession
                            ? "Media needs attention"
                            : "Ready for a conversation"}
                      </h3>
                      <p>
                        {hasSession
                          ? `Browser media: ${media}`
                          : "Connect your microphone to start or resume this lead’s call."}
                      </p>
                    </div>
                    <div className="call-controls">
                      {!hasSession ? (
                        <button
                          className="primary"
                          disabled={
                            !!busy ||
                            !!errors.discovery ||
                            (!!conversation &&
                              !conversational(conversation.state) &&
                              !terminal(conversation.state))
                          }
                          onClick={() =>
                            void action("Connecting microphone", startCall)
                          }
                        >
                          {busy === "Connecting microphone"
                            ? "Connecting…"
                            : "◉ Start / resume call"}
                        </button>
                      ) : (
                        <>
                          {manual && (
                            <button
                              className={speaking ? "recording" : "primary"}
                              disabled={
                                !!busy ||
                                media !== "connected" ||
                                !agentReady ||
                                retryRequired
                              }
                              onClick={() =>
                                void action("Speaking", async () => {
                                  voice.current!.speak(!speaking);
                                  setSpeaking(!speaking);
                                })
                              }
                            >
                              {speaking
                                ? "■ Stop speaking"
                                : "◉ Start speaking"}
                            </button>
                          )}
                          <button
                            disabled={!!busy}
                            onClick={() =>
                              void action("Recovering", async () => {
                                const result = await voice.current!.retry();
                                setRetryRequired(!result.ready);
                                setSpeaking(false);
                              })
                            }
                          >
                            Recover operation
                          </button>
                          <button
                            disabled={!!busy}
                            onClick={() =>
                              void action("Reconnecting", async () => {
                                setSpeaking(false);
                                setAgentReady(false);
                                await voice.current!.negotiate();
                              })
                            }
                          >
                            Reconnect
                          </button>
                          <button
                            className="danger"
                            disabled={!!busy}
                            onClick={() =>
                              void action("Ending call", async () => {
                                await voice.current!.end();
                                setMedia("ended");
                                setAgentReady(false);
                                setSessionStatus(null);
                                setSpeaking(false);
                              })
                            }
                          >
                            End call
                          </button>
                        </>
                      )}
                    </div>
                    <div className="call-settings">
                      <label>
                        <input
                          type="checkbox"
                          checked={manual}
                          disabled={hasSession || !!busy}
                          onChange={(e) => setManual(e.target.checked)}
                        />{" "}
                        Push-to-talk
                      </label>
                      <span>
                        {manual
                          ? "Click start and stop for each turn"
                          : "Automatic voice activity detection"}
                      </span>
                    </div>
                    <audio
                      ref={audio}
                      autoPlay
                      controls
                      aria-label="Agent audio"
                    />
                    <p className="muted-note">
                      {sessionStatus
                        ? `Speech: ${sessionStatus.speech_mode} · LLM: ${sessionStatus.llm_mode}. `
                        : ""}
                      Mock mode uses fixture transcripts and tones. Real mode
                      uses configured server providers.
                    </p>
                    {errors.voice && (
                      <p className="inline-error" role="alert">
                        {errors.voice}
                      </p>
                    )}
                    {errors.providers && (
                      <p className="inline-error">
                        Provider snapshot unavailable. {errors.providers}
                      </p>
                    )}
                  </section>
                  <section className="transcript card">
                    <div className="panel-heading">
                      <h2>Conversation transcript</h2>
                      <span className="live-label">
                        <span className="dot" /> DURABLE HISTORY
                      </span>
                    </div>
                    {conversations.length > 0 && (
                      <label className="conversation-picker">
                        Conversation
                        <select
                          aria-label="Conversation history"
                          value={cid || ""}
                          disabled={locked}
                          onChange={(e) => setCid(e.target.value)}
                        >
                          {conversations.map((c) => (
                            <option key={c.id} value={c.id}>
                              {dateTime(c.created_at)} · {label(c.state)} ·{" "}
                              {c.id.slice(0, 8)}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    {errors.history && (
                      <p className="inline-error" role="alert">
                        History refresh unavailable; retained messages are the
                        last successful read.
                      </p>
                    )}
                    <div
                      className="transcript-scroll"
                      role="log"
                      aria-label="Durable transcript"
                    >
                      {historyMore && messages.length < 500 && (
                        <button
                          className="load-history"
                          disabled={!!busy}
                          onClick={() =>
                            void action("Loading history", olderHistory)
                          }
                        >
                          Load earlier messages
                        </button>
                      )}
                      {!messages.length && (
                        <div className="transcript-empty">
                          <span>☷</span>
                          <h3>
                            {cid
                              ? "The conversation starts here."
                              : "No conversation yet."}
                          </h3>
                          <p>
                            Accepted turns and generated agent replies will
                            appear as they’re persisted.
                          </p>
                        </div>
                      )}
                      {messages.map((message) => (
                        <article
                          key={message.id}
                          className={`message ${message.speaker.toLowerCase()}`}
                        >
                          <div className="message-meta">
                            <strong>
                              {message.speaker === "USER"
                                ? lead?.display_name || "Caller"
                                : message.speaker === "AGENT"
                                  ? "Voice agent"
                                  : label(message.speaker)}
                            </strong>
                            <span>
                              #{message.sequence_number} ·{" "}
                              {dateTime(message.created_at)}
                            </span>
                            <Badge value={message.turn_status} />
                          </div>
                          <p>{message.text}</p>
                          {message.speaker === "AGENT" && (
                            <small>
                              {message.provider} / {message.model} · Generated
                              output
                            </small>
                          )}
                          {message.redacted && (
                            <small>Content redacted by retention policy</small>
                          )}
                        </article>
                      ))}
                      {partial &&
                        !messages.some(
                          (m) => m.speaker === "USER" && m.text === partial,
                        ) && (
                          <article className="message interim">
                            <div className="message-meta">
                              <strong>Live transcript</strong>
                              <span>Awaiting durable history</span>
                            </div>
                            <p>{partial}</p>
                          </article>
                        )}
                    </div>
                    <p className="muted-note">
                      Persisted agent text records generation, not proof that
                      the full reply was heard. Showing up to 500 messages.
                    </p>
                  </section>
                  <section className="card">
                    <div className="panel-heading">
                      <h2>Handoff & follow-up</h2>
                      <span className="section-icon">↗</span>
                    </div>
                    {errors.workflows && (
                      <p className="inline-error">
                        Workflow state unavailable; controls are paused.
                      </p>
                    )}
                    {!workflows.handoffs.length &&
                      !workflows.follow_ups.length && (
                        <p className="empty">
                          No workflow requested. An explicit caller request can
                          create a consultant handoff or follow-up reminder.
                        </p>
                      )}
                    <fieldset disabled={!!errors.workflows}>
                      {workflowRows("handoffs", workflows.handoffs)}
                      {workflowRows("follow-ups", workflows.follow_ups)}
                    </fieldset>
                    {pendingWorkflow.current && (
                      <button
                        disabled={!!busy}
                        onClick={() =>
                          void action("Recovering workflow", async () => {
                            const pending = pendingWorkflow.current!;
                            await api(pending.path, "POST", pending.payload);
                            pendingWorkflow.current = null;
                          })
                        }
                      >
                        Recover same workflow operation
                      </button>
                    )}
                    <p className="muted-note">
                      Follow-ups are durable dashboard reminders. Due dispatch
                      is operator-run; reminders do not place phone calls.
                    </p>
                  </section>
                </div>
                <div className="context-column">
                  <section className="card score-card">
                    <div className="panel-heading">
                      <h2>Qualification score</h2>
                      <Badge
                        value={
                          errors.qualification ? "UNAVAILABLE" : statusLabel
                        }
                      />
                    </div>
                    <div className="score-display">
                      <strong>
                        {errors.qualification
                          ? "—"
                          : (qualification?.score?.score ?? "—")}
                      </strong>
                      <span>/ 60</span>
                    </div>
                    <p>Confirmed profile coverage</p>
                    <div className="meter">
                      <span
                        style={{
                          width: `${qualification?.score ? (qualification.score.score / 60) * 100 : 0}%`,
                        }}
                      />
                    </div>
                    {errors.qualification ? (
                      <p className="inline-error" role="alert">
                        Qualification unavailable. Scoring-dependent actions are
                        paused; no cached score is substituted.
                      </p>
                    ) : (
                      <small>
                        {qualification?.score
                          ? `${qualification.score.rule_version} · ${dateTime(qualification.score.calculated_at)}`
                          : "Not calculated yet"}
                      </small>
                    )}
                    <p className="muted-note">
                      Authoritative in Lead Service. Coverage is not immigration
                      eligibility.
                    </p>
                  </section>
                  <section className="card">
                    <div className="panel-heading">
                      <h2>Lead qualification</h2>
                      <small>
                        {qualification
                          ? `${qualification.completeness}% complete`
                          : "Unavailable"}
                      </small>
                    </div>
                    <div className="qualification-fields">
                      {fields.map((field) => {
                        const answer = qualification?.answers.find(
                          (a) => a.field_key === field,
                        );
                        return (
                          <div key={field}>
                            <div>
                              <small>{label(field)}</small>
                              <strong>
                                {answer
                                  ? valueLabel(answer.value)
                                  : "Not provided"}
                              </strong>
                              {answer?.answer_status === "CONTRADICTORY" && (
                                <small className="conflict">
                                  Conflicting:{" "}
                                  {valueLabel(answer.conflict_value)}
                                </small>
                              )}
                            </div>
                            <Badge value={answer?.answer_status || "MISSING"} />
                          </div>
                        );
                      })}
                    </div>
                    {question && (
                      <div className="next-question">
                        <p className="eyebrow">
                          BACKEND-SELECTED NEXT QUESTION
                        </p>
                        <p>{question}</p>
                      </div>
                    )}
                  </section>
                  <section className="card">
                    <div className="panel-heading">
                      <h2>Conversation state</h2>
                      <button
                        className="icon-button"
                        aria-label="Refresh selected lead"
                        disabled={!!busy}
                        onClick={() => void refresh()}
                      >
                        ↻
                      </button>
                    </div>
                    <Badge value={conversation?.state || "NOT_STARTED"} />
                    <p className="next-action">
                      {conversation?.next_action
                        ? label(conversation.next_action)
                        : conversation
                          ? "No pending business action"
                          : "Start a call to create a conversation"}
                    </p>
                    <dl className="state-details">
                      <div>
                        <dt>Business version</dt>
                        <dd>{conversation?.version ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>Call reconnects</dt>
                        <dd>
                          {activeCall?.reconnect_attempts ??
                            calls?.items[0]?.reconnect_attempts ??
                            "—"}
                        </dd>
                      </div>
                      <div>
                        <dt>Runtime session</dt>
                        <dd>{hasSession ? "This browser" : "None attached"}</dd>
                      </div>
                    </dl>
                    {(errors.calls || errors.conversation) && (
                      <p className="inline-error">
                        Durable state refresh unavailable. Displayed state is
                        the last successful read.
                      </p>
                    )}
                    <details>
                      <summary>Call history ({calls?.total ?? 0})</summary>
                      {calls?.items.map((call) => (
                        <div className="call-history" key={call.id}>
                          <Badge value={call.status} />
                          <small>
                            {dateTime(call.created_at)} · {call.id.slice(0, 8)}
                          </small>
                        </div>
                      ))}
                    </details>
                    {updated && (
                      <p className="muted-note">
                        Last refresh {dateTime(updated)} · Polling every 2.5s
                      </p>
                    )}
                  </section>
                  <section className="card">
                    <div className="panel-heading">
                      <h2>Provider status</h2>
                      <small>SESSION LOCAL</small>
                    </div>
                    {!sessionStatus && (
                      <p className="empty">
                        {hasSession
                          ? "Waiting for the runtime snapshot…"
                          : "Connect a call to observe its provider routers."}
                      </p>
                    )}
                    {sessionStatus &&
                      Object.entries(sessionStatus.providers).map(
                        ([kind, providers]) => (
                          <div className="provider-group" key={kind}>
                            <p className="eyebrow">{kind.toUpperCase()}</p>
                            {providers.map((provider) => (
                              <div className="provider" key={provider.provider}>
                                <div>
                                  <strong>{provider.provider}</strong>
                                  <small>{provider.model}</small>
                                </div>
                                <Badge
                                  value={
                                    provider.status ||
                                    (!provider.enabled
                                      ? "DISABLED"
                                      : provider.circuit_state !== "CLOSED"
                                        ? "UNAVAILABLE"
                                        : provider.consecutive_failures > 0
                                          ? "DEGRADED"
                                          : provider.success_count > 0
                                            ? "HEALTHY"
                                            : "UNKNOWN")
                                  }
                                />
                                <small>
                                  {label(provider.circuit_state)} circuit ·{" "}
                                  {provider.success_count} successes ·{" "}
                                  {provider.failure_count} failures ·{" "}
                                  {provider.failover_count} failovers
                                </small>
                              </div>
                            ))}
                          </div>
                        ),
                      )}
                  </section>
                </div>
              </div>
              <section className="card events-card">
                <div className="panel-heading">
                  <h2>
                    Event timeline <span className="count">{eventTotal}</span>
                  </h2>
                  <span className="live-label">CONVERSATION-OWNED OUTBOX</span>
                </div>
                {errors.events && (
                  <p className="inline-error">Event refresh unavailable.</p>
                )}
                {!events.length ? (
                  <p className="empty">
                    Committed conversation, call and workflow events appear
                    here.
                  </p>
                ) : (
                  <div className="event-table">
                    <div className="event-table-header">
                      <span>Event</span>
                      <span>Aggregate</span>
                      <span>Occurred</span>
                      <span>Delivery</span>
                    </div>
                    {events.map((event) => (
                      <div className="event-row" key={event.event_id}>
                        <strong>{event.event_type}</strong>
                        <span>
                          {event.aggregate_type} · v{event.aggregate_version}
                        </span>
                        <span>{dateTime(event.occurred_at)}</span>
                        <span
                          className={
                            event.published_at
                              ? "delivered"
                              : "pending-delivery"
                          }
                        >
                          {event.published_at
                            ? "Published"
                            : `Pending · ${event.publish_attempts} attempts`}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                <div className="pager">
                  <button
                    disabled={eventOffset === 0}
                    onClick={() => {
                      const offset = Math.max(0, eventOffset - 20);
                      setEventOffset(offset);
                      eventPage.current = offset;
                      void refresh();
                    }}
                  >
                    ← Newer
                  </button>
                  <small>
                    Metadata only · published does not mean consumed
                  </small>
                  <button
                    disabled={
                      eventOffset + 20 >= eventTotal || eventOffset >= 9980
                    }
                    onClick={() => {
                      const offset = eventOffset + 20;
                      setEventOffset(offset);
                      eventPage.current = offset;
                      void refresh();
                    }}
                  >
                    Older →
                  </button>
                </div>
              </section>
              <div className="future-grid">
                <section className="card future diagnostics">
                  <div>
                    <h2>Failure simulation</h2>
                    <p>
                      Faults affect only this media session. Primary-provider
                      attempts use the existing retry/failover policy; mock mode
                      has one provider. Reset stops new injections; circuit
                      cooldown still applies. Recovery finishes saved work
                      without replaying speech.
                    </p>
                  </div>
                  {!sessionStatus?.faults_enabled && (
                    <p>
                      Enable DEMO_FAULTS_ENABLED=1 in local/test Gateway
                      configuration and connect a call.
                    </p>
                  )}
                  <label>
                    Demo fault
                    <select
                      aria-label="Demo fault"
                      value={faultChoice}
                      onChange={(e) => setFaultChoice(e.target.value)}
                      disabled={!!busy || !sessionStatus?.faults_enabled}
                    >
                      <option value="llm:unavailable">
                        Primary LLM unavailable
                      </option>
                      <option value="stt:unavailable">
                        Primary STT unavailable
                      </option>
                      <option value="tts:unavailable">
                        Primary TTS unavailable
                      </option>
                      <option value="llm:latency">
                        LLM latency (+1 second)
                      </option>
                      <option value="tts:latency">
                        TTS latency (+1 second)
                      </option>
                      <option value="dependency:timeout">
                        Conversation apply timeout
                      </option>
                    </select>
                  </label>
                  <button
                    disabled={
                      !!busy ||
                      !sessionStatus?.faults_enabled ||
                      sessionStatus.ending ||
                      retryRequired
                    }
                    onClick={() =>
                      void action("Arming fault", async () => {
                        const [target, mode] = faultChoice.split(":");
                        await voice.current!.armFault(target, mode);
                        setNotice(
                          "Fault armed for up to two attempts / 60 seconds. Start a new turn to exercise it.",
                        );
                      })
                    }
                  >
                    Arm fault
                  </button>
                  <button
                    disabled={!!busy || !sessionStatus?.faults_enabled}
                    onClick={() =>
                      void action("Resetting faults", async () => {
                        await voice.current!.resetFault();
                        setNotice(
                          "New fault injections stopped. Use Recover operation for saved work, or Reconnect for media.",
                        );
                      })
                    }
                  >
                    Reset fault
                  </button>
                  <button
                    disabled={
                      !!busy ||
                      !sessionStatus?.faults_enabled ||
                      media !== "connected"
                    }
                    onClick={() =>
                      void action("Disconnecting media", async () => {
                        voice.current!.disconnectForDemo();
                        setAgentReady(false);
                        setNotice(
                          "Local media disconnected. Reconnect preserves the durable call and conversation.",
                        );
                      })
                    }
                  >
                    Disconnect media
                  </button>
                  {sessionStatus?.active_fault && (
                    <p role="status">
                      Armed: {sessionStatus.active_fault.target} /{" "}
                      {sessionStatus.active_fault.mode} ·{" "}
                      {sessionStatus.active_fault.remaining_attempts} attempts ·{" "}
                      {Math.ceil(sessionStatus.active_fault.expires_in_seconds)}{" "}
                      seconds
                    </p>
                  )}
                  <p className="muted-note">
                    These simulate boundary faults. Actual database, Redis and
                    worker outages use operator deployment commands; the
                    dashboard does not control containers.
                  </p>
                  <details>
                    <summary>Operational measurements</summary>
                    {errors.observability && (
                      <p role="alert">Operational snapshots unavailable.</p>
                    )}
                    {[
                      operations?.gateway,
                      operations?.lead,
                      operations?.conversation,
                      sessionStatus?.diagnostics,
                    ].map((snapshot, index) => (
                      <div key={index}>
                        <strong>
                          {snapshot?.service || "Service snapshot unavailable"}
                        </strong>
                        {snapshot && (
                          <>
                            <p>
                              Process/session memory ·{" "}
                              {Math.floor(snapshot.uptime_seconds)} seconds
                              uptime
                            </p>
                            {snapshot.metrics.slice(-12).map((metric) => (
                              <p key={metric.operation}>
                                {metric.operation} · {metric.count} operations ·{" "}
                                {metric.errors} errors · mean{" "}
                                {(
                                  metric.total_ms / Math.max(1, metric.count)
                                ).toFixed(0)}{" "}
                                ms · max {metric.max_ms.toFixed(0)} ms
                              </p>
                            ))}
                            <p>
                              Latest: {snapshot.recent.at(-1)?.operation} /{" "}
                              {snapshot.recent.at(-1)?.outcome} · request{" "}
                              {snapshot.recent.at(-1)?.request_id || "none"}
                            </p>
                          </>
                        )}
                      </div>
                    ))}
                  </details>
                </section>
                <section className="card future diagnostics">
                  <div>
                    <h2>Evaluation</h2>
                    <p>
                      Repeatable synthetic scenarios verify qualification,
                      scoring, provider failover, recovery and concurrent calls.
                      Run the repository evaluation command to generate a
                      report.
                    </p>
                  </div>
                  <code>make evaluate EVALUATION_SUITE=all</code>
                </section>
              </div>
              {activity.length > 0 && (
                <details className="activity">
                  <summary>Browser media activity ({activity.length})</summary>
                  {activity
                    .slice()
                    .reverse()
                    .map((item, index) => (
                      <div key={index}>
                        <time>{dateTime(item.at)}</time>
                        <span>{item.text}</span>
                      </div>
                    ))}
                </details>
              )}
            </div>
          )}
        </div>
        <footer>
          VOX / Lead operations{" "}
          <span>PostgreSQL business state · Gateway APIs · Pipecat media</span>
        </footer>
      </main>
    </div>
  );
}
