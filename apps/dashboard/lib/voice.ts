import { api, ApiError } from "./api";
import type { SessionInfo, SessionStatus } from "./types";

type Answer = { sdp: string; type: RTCSdpType; pc_id: string };
export type VoiceEvent = { type: string; [key: string]: unknown };

/** One browser media owner. Tokens/SDP stay in memory; accepted offers retry the same payload. */
export class VoiceClient {
  session: SessionInfo | null = null;
  peer: RTCPeerConnection | null = null;
  channel: RTCDataChannel | null = null;
  mic: MediaStream | null = null;
  pcId: string | null = null;
  private pendingOffer: {
    sdp: string;
    type: string;
    pc_id: string | null;
    restart_pc: boolean;
  } | null = null;
  private disposed = false;
  private negotiated = false;
  private ending = false;
  constructor(
    private notify: (event: VoiceEvent) => void,
    private audio: HTMLAudioElement,
  ) {}

  async connect(conversationId: string, callId: string, manual: boolean) {
    if (this.session)
      throw new Error("Recover or end the current media session first.");
    this.mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
      video: false,
    });
    try {
      this.session = await api<SessionInfo>("/voice/sessions", "POST", {
        conversation_id: conversationId,
        call_id: callId,
        manual_turns: manual,
      });
      if (this.disposed) {
        await this.end();
        return;
      }
      this.notify({
        type: "session",
        speech_mode: this.session.speech_mode,
        llm_mode: this.session.llm_mode,
      });
      await this.negotiate();
    } catch (error) {
      if (!this.session) this.stopLocal();
      throw error;
    }
  }

  private base() {
    if (!this.session) throw new Error("No active media session.");
    return "/voice/sessions/" + this.session.session_id;
  }

  async negotiate() {
    if (this.ending)
      throw new Error(
        "Call closure is pending. Retry End call before reconnecting.",
      );
    if (!this.mic?.getTracks().some((track) => track.readyState === "live")) {
      this.mic = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: false,
      });
    }
    // If signaling failed ambiguously, preserve the exact peer/SDP and retry its payload.
    if (!this.pendingOffer) {
      this.peer?.close();
      const pc = new RTCPeerConnection({ iceServers: [] });
      this.peer = pc;
      for (const track of this.mic.getTracks()) pc.addTrack(track, this.mic);
      this.channel = pc.createDataChannel("chat");
      this.channel.onmessage = (event) => {
        if (this.peer !== pc) return;
        try {
          if (typeof event.data !== "string" || event.data.length > 131072)
            return;
          const message = JSON.parse(event.data) as VoiceEvent;
          if (message.type !== "signalling") this.notify(message);
        } catch {
          this.notify({ type: "error", code: "invalid_media_notification" });
        }
      };
      pc.ontrack = (event) => {
        if (this.peer !== pc) return;
        this.audio.srcObject =
          event.streams[0] || new MediaStream([event.track]);
        this.audio
          .play()
          .catch(() => this.notify({ type: "playback_blocked" }));
      };
      pc.onconnectionstatechange = () => {
        if (this.peer === pc)
          this.notify({ type: "media", state: pc.connectionState });
      };
      await pc.setLocalDescription(await pc.createOffer());
      if (pc.iceGatheringState !== "complete")
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => {
            pc.removeEventListener("icegatheringstatechange", update);
            reject(
              new Error("ICE gathering timed out. Reconnect to try again."),
            );
          }, 10000);
          const update = () => {
            if (pc.iceGatheringState === "complete") {
              clearTimeout(timer);
              pc.removeEventListener("icegatheringstatechange", update);
              resolve();
            }
          };
          pc.addEventListener("icegatheringstatechange", update);
        });
      this.pendingOffer = {
        sdp: pc.localDescription!.sdp,
        type: "offer",
        pc_id: this.pcId,
        restart_pc: this.negotiated,
      };
    }
    const answer = await api<Answer>(
      this.base() + "/offer",
      "POST",
      this.pendingOffer,
      this.session!.token,
    );
    if (this.disposed) return;
    await this.peer!.setRemoteDescription({
      sdp: answer.sdp,
      type: answer.type,
    });
    this.pcId = answer.pc_id;
    this.pendingOffer = null;
    this.negotiated = true;
  }

  speak(start: boolean) {
    if (this.channel?.readyState !== "open")
      throw new Error("Media is not connected.");
    this.channel.send(JSON.stringify({ type: start ? "start" : "stop" }));
  }
  status() {
    return api<SessionStatus>(
      this.base() + "/status",
      "GET",
      undefined,
      this.session!.token,
    );
  }
  retry() {
    return api<{ ready: boolean }>(
      this.base() + "/retry",
      "POST",
      undefined,
      this.session!.token,
    );
  }

  private stopLocal() {
    this.peer?.close();
    this.peer = null;
    this.mic?.getTracks().forEach((track) => track.stop());
    this.mic = null;
    this.channel = null;
    this.audio.srcObject = null;
  }
  async end() {
    this.ending = true;
    try {
      if (this.session) {
        try {
          await api(this.base(), "DELETE", undefined, this.session.token);
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 404) throw error;
        }
      }
      this.session = null;
      this.pcId = null;
      this.pendingOffer = null;
      this.negotiated = false;
    } finally {
      // An ambiguous end remains retryable, but never leaves the microphone active.
      this.stopLocal();
    }
  }
  dispose() {
    this.disposed = true;
    this.stopLocal();
    if (this.session) {
      fetch("/api" + this.base(), {
        method: "DELETE",
        keepalive: true,
        headers: { Authorization: "Bearer " + this.session.token },
      }).catch(() => {});
    }
  }
}
