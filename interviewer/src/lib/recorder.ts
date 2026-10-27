export type RecordState = "idle" | "recording" | "transcribing";

const MIME_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];

// Open the microphone and start recording; throws if the mic can't be opened.
// When the returned recorder is stopped, the mic is released and `onStop` gets the
// recording, or null if no audio was captured.
export async function startRecording(
  onStop: (blob: Blob | null) => void,
): Promise<MediaRecorder> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const mimeType = MIME_TYPES.find((t) => MediaRecorder.isTypeSupported(t)) ?? "";
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data);
  };
  recorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
    onStop(chunks.length ? new Blob(chunks, { type: mimeType || "audio/webm" }) : null);
  };
  recorder.start(250); // 250ms timeslice ensures data flows reliably
  return recorder;
}
