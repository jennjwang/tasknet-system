import { useRef, useState } from "react";
import { TaskItem } from "../../types";
import { transcribeAudio } from "../../lib/api";
import { RecordState, startRecording } from "../../lib/recorder";
import { ArrowRightIcon, MicIcon, StopIcon } from "../ui";

// The last screen: the tasks they kept, and a box (typed or dictated) for anything the list missed.
export function ReviewAndAddScreen({
  confirmedTasks,
  extraTasks,
  extraInput,
  onExtraInputChange,
  onAddExtra,
  onRemoveExtra,
  onRemoveConfirmed,
  onSubmit,
}: {
  confirmedTasks: TaskItem[];
  extraTasks: { name: string; addedAt: number }[];
  extraInput: string;
  onExtraInputChange: (v: string) => void;
  onAddExtra: (v: string) => void;
  onRemoveExtra: (idx: number) => void;
  onRemoveConfirmed: (idx: number) => void;
  onSubmit: () => void;
}) {
  const totalDisplayedSoFar = confirmedTasks.length;
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [recordState, setRecordState] = useState<RecordState>("idle");
  const [recordError, setRecordError] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);

  // Mode-switching primary button: while there's draft text in the input, the
  // button commits it as a task and clears the input — it does NOT submit.
  // Only when the input is empty does the button finalize the response. This
  // prevents accidentally submitting in the middle of typing a task.
  const draft = extraInput.trim();
  const hasDraft = draft.length > 0;
  const handlePrimary = () => {
    if (hasDraft) {
      onAddExtra(draft);
      return;
    }
    onSubmit();
  };

  const toggleRecording = async () => {
    if (recordState === "recording") {
      mediaRecorderRef.current?.stop();
      return;
    }
    if (recordState === "transcribing") return;
    try {
      mediaRecorderRef.current = await startRecording(async (blob) => {
        if (!blob) {
          setRecordState("idle");
          return;
        }
        setRecordState("transcribing");
        try {
          const text = await transcribeAudio(blob);
          if (text.trim()) {
            onExtraInputChange(extraInput ? extraInput + " " + text : text);
            setTimeout(() => textareaRef.current?.focus(), 50);
          }
        } catch {
          setRecordError("Transcription failed — try typing instead.");
        } finally {
          setRecordState("idle");
        }
      });
      setRecordError(null);
      setRecordState("recording");
    } catch {
      setRecordError("Mic access denied — please type your answer.");
    }
  };

  return (
    // my-auto centers vertically; the extra bottom padding on the scroll
    // container (see isExhausted branch) biases it slightly above center.
    <div className="w-full my-auto animate-fadeSlideIn">
      {totalDisplayedSoFar > 0 && (
        <section>
          <h3 className="text-[1.35rem] font-light text-slate-800 leading-snug tracking-tight">
            Here are your tasks so far
          </h3>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-0.5 mt-1.5">
            <p className="text-sm text-slate-500">
              We found {totalDisplayedSoFar} task
              {totalDisplayedSoFar !== 1 ? "s" : ""} from this interaction.
            </p>
            <p className="text-xs text-slate-400">
              Use the × to remove a task.
            </p>
          </div>

          <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {confirmedTasks.map((t, i) => (
              <div
                key={`${t.name}-${i}`}
                className="group relative px-4 py-3 rounded-xl bg-white border border-slate-200 hover:border-indigo-200 hover:shadow-sm transition"
              >
                <p className="text-sm text-slate-800 leading-snug pr-6">
                  {t.name}
                </p>
                {t.status === "edited" && (
                  <span className="mt-1.5 inline-block text-[10px] font-semibold uppercase tracking-wider text-amber-600">
                    edited
                  </span>
                )}
                <button
                  onClick={() => onRemoveConfirmed(i)}
                  aria-label="Remove task"
                  className="absolute top-2 right-2 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity w-7 h-7 sm:w-5 sm:h-5 flex items-center justify-center rounded-full text-slate-400 sm:text-slate-300 hover:text-red-400 hover:bg-red-50"
                >
                  <svg
                    viewBox="0 0 16 16"
                    className="w-3 h-3"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  >
                    <line x1="4" y1="4" x2="12" y2="12" />
                    <line x1="12" y1="4" x2="4" y2="12" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* What else fills your week. */}
      <section className="mt-16">
        <div className="flex items-center justify-between gap-4">
          <h3 className="text-[1.35rem] font-light text-slate-800 leading-snug tracking-tight">
            What else fills your week?
          </h3>
        </div>
        <p className="text-sm text-slate-500 mt-3 leading-relaxed">
          We almost certainly missed something. Add the tasks that didn't make
          our list.
        </p>
        <div className="mt-4 relative">
          <textarea
            ref={textareaRef}
            rows={3}
            value={extraInput}
            onChange={(e) => onExtraInputChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onAddExtra(extraInput);
              }
            }}
            placeholder={
              recordState === "recording"
                ? "Recording — click the mic again to stop"
                : recordState === "transcribing"
                  ? "Transcribing…"
                  : "Type a task and press Enter — or use the mic to dictate"
            }
            disabled={recordState === "transcribing"}
            className="w-full pl-4 pr-14 py-3 text-sm text-slate-700 placeholder:text-slate-400 bg-white border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-200 focus:border-indigo-300 transition resize-none disabled:opacity-60"
          />
          <button
            type="button"
            onClick={toggleRecording}
            disabled={recordState === "transcribing"}
            aria-label={
              recordState === "recording" ? "Stop recording" : "Record audio"
            }
            title={
              recordState === "recording" ? "Stop recording" : "Record audio"
            }
            className={`absolute right-2.5 bottom-2.5 w-9 h-9 rounded-full flex items-center justify-center transition active:scale-[0.95] disabled:cursor-not-allowed
              ${
                recordState === "recording"
                  ? "bg-red-500 text-white shadow-sm shadow-red-200"
                  : recordState === "transcribing"
                    ? "bg-slate-100 text-slate-300"
                    : "bg-indigo-50 text-indigo-600 hover:bg-indigo-100 hover:text-indigo-700"
              }`}
          >
            {recordState === "transcribing" ? (
              <div className="flex items-center justify-center gap-[2px] h-5">
                {[0, 120, 240, 360].map((delay, i) => (
                  <span
                    key={i}
                    className="w-[2px] h-4 bg-indigo-500 rounded-full animate-waveBar"
                    style={{ animationDelay: `${delay}ms` }}
                  />
                ))}
              </div>
            ) : recordState === "recording" ? (
              <StopIcon className="w-3.5 h-3.5" />
            ) : (
              <MicIcon className="w-4 h-4" />
            )}
          </button>
        </div>
        {recordError && (
          <p className="mt-1.5 text-xs text-red-500">{recordError}</p>
        )}

        {extraTasks.length > 0 && (
          <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {extraTasks.map((t, i) => (
              <div
                key={`${t.name}-${i}`}
                className="group relative pl-4 pr-10 py-3 rounded-xl bg-white border border-indigo-200 hover:shadow-sm transition"
              >
                <p className="text-sm text-slate-800 leading-snug">{t.name}</p>
                <button
                  type="button"
                  onClick={() => onRemoveExtra(i)}
                  aria-label={`Remove ${t.name}`}
                  title="Remove task"
                  className="absolute top-1/2 -translate-y-1/2 right-2 w-6 h-6 flex items-center justify-center rounded-full text-slate-300 hover:text-red-500 hover:bg-red-50 transition"
                >
                  <svg
                    viewBox="0 0 20 20"
                    className="w-3.5 h-3.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                  >
                    <line x1="6" y1="6" x2="14" y2="14" />
                    <line x1="14" y1="6" x2="6" y2="14" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="mt-8 flex items-center justify-end gap-3">
        <button
          onClick={handlePrimary}
          aria-label={hasDraft ? "Add task" : "Submit"}
          className={`shrink-0 inline-flex items-center gap-2 px-7 py-3 text-white text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-md cursor-pointer
            ${
              hasDraft
                ? "bg-gradient-to-br from-indigo-400 to-indigo-500 hover:from-indigo-500 hover:to-indigo-600 shadow-indigo-100"
                : "bg-gradient-to-br from-indigo-500 to-indigo-600 hover:from-indigo-600 hover:to-indigo-700 shadow-indigo-200/70"
            }`}
        >
          {hasDraft ? (
            <>
              Add task
              <svg
                className="w-4 h-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </>
          ) : (
            <>
              Submit
              <ArrowRightIcon strokeWidth={2.4} />
            </>
          )}
        </button>
      </div>
    </div>
  );
}
