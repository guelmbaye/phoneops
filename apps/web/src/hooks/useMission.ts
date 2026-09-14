"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { EVENT_TYPES, TERMINAL_STATUSES } from "@/lib/types";
import type { RecoveryView, StreamEvent } from "@/lib/types";

export type ConnectionState = "connecting" | "live" | "closed";

interface MissionState {
  view: RecoveryView | null;
  error: string | null;
  connection: ConnectionState;
  /** Set for ~3.2s when the engine invalidates a strategy — drives the hero moment. */
  invalidation: StreamEvent | null;
  refresh: () => Promise<void>;
}

/** How long the ⚠ RECOVERY PLAN INVALIDATED overlay stays up (DOCUMENT 04 §17). */
const HERO_DURATION_MS = 3200;

/**
 * Subscribes to the mission stream and re-reads the aggregate on every event.
 *
 * Deliberately dumb: the UI never derives mission state from event payloads, it
 * asks the backend for the truth. Events are only a "something changed" signal
 * plus the trigger for the invalidation overlay.
 */
export function useMission(missionId: string | null): MissionState {
  const [view, setView] = useState<RecoveryView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [invalidation, setInvalidation] = useState<StreamEvent | null>(null);

  const pending = useRef(false);
  const dirty = useRef(false);
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  /** Highest sequence already accounted for. Anything at or below it is history. */
  const seen = useRef(0);

  const refresh = useCallback(async () => {
    if (!missionId) return;
    // Coalesce, never drop. Events arrive in bursts and the last one is the one
    // that matters: skipping it leaves Mission Control showing EVALUATING under
    // a RECOVERED banner, with no further event coming to correct it.
    if (pending.current) {
      dirty.current = true;
      return;
    }
    pending.current = true;
    try {
      setView(await api.getMission(missionId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to reach the recovery engine.");
    } finally {
      pending.current = false;
      if (dirty.current) {
        dirty.current = false;
        void refreshRef.current();
      }
    }
  }, [missionId]);

  refreshRef.current = refresh;

  useEffect(() => {
    if (!missionId) return;
    let cancelled = false;
    let source: EventSource | null = null;

    const onEvent = (raw: MessageEvent) => {
      setConnection("live");
      let event: StreamEvent | null = null;
      try {
        event = JSON.parse(raw.data) as StreamEvent;
      } catch {
        return;
      }
      // The stream replays history on connect, and browsers silently reconnect.
      // Without a monotonic watermark, reopening a finished mission replays
      // STRATEGY_INVALIDATED and pins a full-screen alert over a recovered
      // mission - permanently, since each replay outruns the 3.2s dismissal.
      if (event.sequence <= seen.current) return;
      seen.current = event.sequence;

      if (event.type === "MISSION_HEARTBEAT") return;
      if (event.type === "STRATEGY_INVALIDATED") setInvalidation(event);
      void refreshRef.current();
    };

    // Read the aggregate first, then stream only what happened after it. The
    // order matters: opening the stream first would race the watermark and
    // could suppress a genuinely live invalidation.
    void (async () => {
      let start = 0;
      try {
        const first = await api.getMission(missionId);
        if (cancelled) return;
        setView(first);
        setError(null);
        start = first.timeline.reduce((max, e) => Math.max(max, e.sequence), 0);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Unable to reach the recovery engine.");
      }
      if (cancelled) return;

      seen.current = start;
      source = new EventSource(`/api/recovery-missions/${missionId}/stream?since=${start}`);
      for (const type of EVENT_TYPES) source.addEventListener(type, onEvent);
      source.onopen = () => setConnection("live");
      source.onerror = () => setConnection("closed");
    })();

    return () => {
      cancelled = true;
      if (source) {
        for (const type of EVENT_TYPES) source.removeEventListener(type, onEvent);
        source.close();
      }
    };
  }, [missionId]);

  // The dismissal timer belongs to the state it dismisses. Owning it inside the
  // stream effect meant a reconnect could clear the timer while leaving the
  // overlay mounted — a full-screen alert stuck over Mission Control at exactly
  // the moment of the demo that has to keep moving.
  useEffect(() => {
    if (!invalidation) return;
    const timer = setTimeout(() => setInvalidation(null), HERO_DURATION_MS);
    return () => clearTimeout(timer);
  }, [invalidation]);

  // Safety net: if the stream drops before a terminal state, keep polling slowly
  // so the demo never freezes on a stale screen.
  useEffect(() => {
    if (!missionId || !view) return;
    if (TERMINAL_STATUSES.has(view.mission.status)) return;
    if (connection === "live") return;
    const id = setInterval(() => void refreshRef.current(), 2500);
    return () => clearInterval(id);
  }, [missionId, view, connection]);

  return { view, error, connection, invalidation, refresh };
}

/** Local per-second countdown seeded from the server's remaining window. */
export function useCountdown(remainingSeconds: number | undefined): number {
  const [seconds, setSeconds] = useState(remainingSeconds ?? 0);

  useEffect(() => {
    if (remainingSeconds === undefined) return;
    setSeconds(remainingSeconds);
  }, [remainingSeconds]);

  useEffect(() => {
    const id = setInterval(() => setSeconds((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(id);
  }, []);

  return seconds;
}
