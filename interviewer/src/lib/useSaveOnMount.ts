import { useEffect, useRef } from "react";
import { useWorkflowStore } from "../store";
import { saveSession } from "./api";

// Save the session once when a screen mounts — a safety net in case an earlier
// save failed (the server upserts on sessionId). `payload` defaults to the full
// export.
export function useSaveOnMount(payload?: () => Record<string, unknown>) {
  const sessionId = useWorkflowStore((s) => s.sessionId);
  const getExportData = useWorkflowStore((s) => s.getExportData);
  const saved = useRef(false);
  useEffect(() => {
    if (saved.current) return;
    saved.current = true;
    const data = payload ? payload() : getExportData();
    saveSession(sessionId, data).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
