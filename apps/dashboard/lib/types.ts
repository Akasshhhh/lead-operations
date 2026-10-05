export type Lead = {
  id: string;
  display_name: string;
  synthetic_profile_key: string;
  intent: string | null;
  target_country: string | null;
  preferred_language: string;
  status: string;
  version: number;
  created_at: string;
  updated_at: string;
};
export type Conversation = {
  id: string;
  lead_id: string;
  state: string;
  version: number;
  next_action: string | null;
  created_at: string;
  completed_at: string | null;
  failure_reason: string | null;
};
export type Call = {
  id: string;
  conversation_id: string;
  status: string;
  version: number;
  transport: string;
  reconnect_attempts: number;
  created_at: string;
  failure_reason: string | null;
};
export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};
export type Calls = Page<Call> & { active_call: Call | null };
export type Message = {
  id: string;
  conversation_id: string;
  call_id: string | null;
  speaker: string;
  text: string;
  sequence_number: number;
  turn_status: string;
  redacted: boolean;
  created_at: string;
  provider: string | null;
  model: string | null;
};
export type History = {
  items: Message[];
  has_more: boolean;
  next_before_sequence: number | null;
};
export type Qualification = {
  lead_id: string;
  version: number;
  completeness: number;
  status: string;
  answers: {
    field_key: string;
    value: unknown;
    conflict_value: unknown;
    answer_status: string;
    source: string;
  }[];
  score: {
    score: number;
    classification: string;
    rule_version: string;
    calculated_at: string;
    reasons: unknown[];
  } | null;
};
export type Context = {
  lead: Lead;
  plan: {
    qualification: Qualification;
    next_question: string | null;
    missing_fields: string[];
    contradictory_fields: string[];
    provisional_fields: string[];
  };
};
export type Workflow = {
  id: string;
  status: string;
  reason: string | null;
  summary?: string;
  scheduled_at?: string;
  attempt?: number;
  max_attempts?: number;
  last_error?: string | null;
};
export type Workflows = { handoffs: Workflow[]; follow_ups: Workflow[] };
export type DomainEvent = {
  event_id: string;
  event_type: string;
  aggregate_type: string;
  aggregate_version: number;
  occurred_at: string;
  published_at: string | null;
  publish_attempts: number;
};
export type Provider = {
  provider: string;
  model: string;
  enabled: boolean;
  circuit_state: string;
  status?: string;
  success_count: number;
  failure_count: number;
  consecutive_failures: number;
  failover_count: number;
  last_error: string | null;
};
export type SessionInfo = {
  session_id: string;
  token: string;
  speech_mode: string;
  llm_mode: string;
};
export type SessionStatus = {
  session_id: string;
  conversation_id: string;
  call_id: string;
  ready: boolean;
  closed: boolean;
  ending: boolean;
  pending_operation: boolean;
  media_state: string;
  providers: { llm: Provider[]; stt: Provider[]; tts: Provider[] };
  expires_in_seconds: number;
  llm_mode: string;
  speech_mode: string;
  scope: string;
};
