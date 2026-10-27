import { Fragment, useEffect, useRef, useState } from "react";
import { transcribeAudio } from "../../../interviewer/src/lib/api"; // the interview client's /api/transcribe call (one copy, shared)

type RecordState = "idle" | "recording" | "transcribing";

// Occupation screener: a short lead-in, then two questions (job title and field, primary
// responsibilities) asked one at a time in the interview app's format — word-by-word reveal, answered
// by voice or typing. The question wording matches the interview guide's `jobTitle` and
// `responsibilities` fields. Hands the answers to onPass; the occupation pick that follows does the
// routing. Renders inner content only — wrap in a Frame.
const INTRO_SCREENS = [
  "In a moment, an AI interviewer will ask you a few questions about your job and the tasks you do. These few questions are the only ones about your own job: we use them to identify your occupation.",
  "For the interview, please answer about the specific job you were recruited for, not other work you do.",
  "After that, the study switches from your own job to the occupation in general. You'll be shown <b>that occupation's task list</b> and asked, task by task, whether it is relevant to the occupation.",
  "You don't have to go through all tasks in one sitting. Your answers are saved as you go — close the tab whenever you need to, then reopen the same link and you'll pick up where you left off.",
];

// Placeholders describe one (unrelated) nurse so they don't cue the study's occupations. Exported so the
// occupation pick can show the candidate model the questions as asked.
export const QUESTIONS = [
  {
    key: "title" as const,
    question: "To start, what's your job and what field is it in?",
    placeholder:
      "e.g. I'm a registered nurse on a medical ward at a large public hospital.",
    minChars: 1,
  },
  {
    key: "duties" as const,
    question: "What are your primary responsibilities at work?",
    placeholder:
      "e.g. I care for patients on a hospital ward, give medications, monitor vitals, and coordinate with doctors on treatment plans.",
    minChars: 3,
  },
];

// Reveals text one word at a time, each word rising and fading in (staggered).
// Same treatment as the interview's question card.
const POP_START = 30;
const POP_STAGGER = 38;
const POP_FADE = 460;
function PopInText({ text }: { text: string }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const start = setTimeout(() => setShown(true), POP_START);
    return () => clearTimeout(start);
  }, []);
  // The screens are plain strings, but a phrase can be emphasised with <b>…</b>. The tags are the
  // ONLY markup this understands: they are stripped here and the words between them render bold —
  // anything else would reach the participant verbatim, since these spans are text, not HTML.
  const words: { word: string; bold: boolean }[] = [];
  let bold = false;
  for (const raw of text.split(" ")) {
    let word = raw;
    if (word.includes("<b>")) {
      bold = true;
      word = word.replace("<b>", "");
    }
    const closes = word.includes("</b>");
    if (closes) word = word.replace("</b>", "");
    words.push({ word, bold });
    if (closes) bold = false;
  }
  return (
    <>
      {words.map(({ word, bold: isBold }, i) => (
        <Fragment key={i}>
          <span
            className="inline-block"
            style={{
              opacity: shown ? 1 : 0,
              transform: shown ? "translateY(0)" : "translateY(10px)",
              transition: `opacity ${POP_FADE}ms ease, transform ${POP_FADE}ms ease`,
              transitionDelay: `${i * POP_STAGGER}ms`,
            }}
          >
            {isBold ? <strong>{word}</strong> : word}
          </span>{" "}
        </Fragment>
      ))}
    </>
  );
}

function MicOrb({
  state,
  onToggle,
}: {
  state: RecordState;
  onToggle: () => void;
}) {
  const isRecording = state === "recording";
  const isTranscribing = state === "transcribing";
  return (
    <div className="flex flex-col items-center gap-3 select-none">
      <div className="relative flex items-center justify-center">
        {isRecording && (
          <>
            <span className="absolute w-36 h-36 rounded-full bg-indigo-100 animate-ping opacity-30" />
            <span className="absolute w-28 h-28 rounded-full bg-indigo-100 animate-ping opacity-50 [animation-delay:250ms]" />
          </>
        )}
        <button
          type="button"
          onClick={onToggle}
          disabled={isTranscribing}
          className={`relative z-10 w-20 h-20 rounded-full flex items-center justify-center transition-all duration-200
            ${
              isRecording
                ? "bg-red-500 shadow-lg shadow-red-200 scale-105"
                : isTranscribing
                  ? "bg-indigo-100 cursor-not-allowed"
                  : "bg-indigo-50 hover:bg-indigo-100 hover:scale-105 active:scale-95 shadow-sm"
            }
            disabled:opacity-50`}
        >
          {isTranscribing ? (
            <div className="flex items-center justify-center gap-[3px] h-7">
              {[0, 120, 240, 360, 480].map((delay, i) => (
                <span
                  key={i}
                  className="w-[3px] h-6 bg-indigo-500 rounded-full animate-waveBar"
                  style={{ animationDelay: `${delay}ms` }}
                />
              ))}
            </div>
          ) : isRecording ? (
            <svg
              className="w-6 h-6 text-white"
              viewBox="0 0 24 24"
              fill="currentColor"
            >
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          ) : (
            <svg
              className="w-8 h-8 text-indigo-600"
              viewBox="0 0 24 24"
              fill="none"
            >
              <rect
                x="9"
                y="2"
                width="6"
                height="12"
                rx="3"
                fill="currentColor"
              />
              <path
                d="M5 10a7 7 0 0 0 14 0"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                fill="none"
              />
              <line
                x1="12"
                y1="19"
                x2="12"
                y2="22"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
              <line
                x1="9"
                y1="22"
                x2="15"
                y2="22"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          )}
        </button>
      </div>
      <span
        className={`text-sm font-medium tracking-wide transition-colors ${
          isRecording
            ? "text-red-500"
            : isTranscribing
              ? "text-indigo-400"
              : "text-indigo-600"
        }`}
      >
        {isRecording
          ? "Recording — click to stop"
          : isTranscribing
            ? "Transcribing…"
            : "Start Recording"}
      </span>
    </div>
  );
}

// Auto-focus the answer box on pointer devices only — on touch it pops the
// on-screen keyboard and scrolls the viewport at every question transition.
const AUTOFOCUS_ANSWER =
  typeof window !== "undefined" &&
  !!window.matchMedia &&
  window.matchMedia("(pointer: fine)").matches;

export function OccupationScreener({
  onPass,
}: {
  onPass: (info: { title: string; duties: string }) => void;
}) {
  // 0 .. INTRO_SCREENS.length-1 walks the lead-in; the questions start once it is done.
  const [introStep, setIntroStep] = useState(0);
  const [inIntro, setInIntro] = useState(true);
  const [idx, setIdx] = useState(0);
  // A ref, so the hand-off after the last question reads the answer set in the same tick.
  const answersRef = useRef<string[]>([]);
  const [input, setInput] = useState("");
  const [showTextInput, setShowTextInput] = useState(false);
  const [recordState, setRecordState] = useState<RecordState>("idle");
  const [recordTranscribeError, setRecordTranscribeError] = useState<
    string | null
  >(null);
  const [questionVisible, setQuestionVisible] = useState(true);

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  // Once recording turns out to be impossible in this browser, every later
  // question opens in typing mode rather than making them re-open the text box.
  const micUnavailableRef = useRef(false);

  const current = QUESTIONS[idx];
  const progress = idx / QUESTIONS.length;

  /** Report a mic failure the participant can't fix by changing a setting. */
  const reportMicUnusable = (message: string) => {
    micUnavailableRef.current = true;
    setRecordTranscribeError(message);
    setRecordState("idle");
  };

  // Size the auto-grow textarea to its content — or, when empty, to its
  // (possibly wrapping) placeholder, so a multi-line placeholder isn't clipped.
  const autosizeInput = () => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    const borderY = el.offsetHeight - el.clientHeight;
    const fit = (content: number) => `${content + borderY}px`;
    if (el.value) {
      el.style.height = fit(el.scrollHeight);
    } else {
      el.value = el.placeholder;
      el.style.height = fit(el.scrollHeight);
      el.value = "";
    }
  };

  useEffect(() => {
    autosizeInput();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, idx, showTextInput]);

  const advance = async (value: string) => {
    const trimmed = value.trim();
    if (trimmed.length < current.minChars) return;

    answersRef.current[idx] = trimmed;
    setInput("");
    // Back to voice-first for the next question — unless recording is impossible
    // here, in which case stay in typing mode.
    setShowTextInput(micUnavailableRef.current);
    setRecordTranscribeError(null);

    if (idx === QUESTIONS.length - 1) {
      const [title = "", duties = ""] = answersRef.current;
      onPass({ title: title.trim(), duties: duties.trim() });
      return;
    }
    setQuestionVisible(false);
    await new Promise((r) => setTimeout(r, 260));
    setIdx((i) => i + 1);
    setQuestionVisible(true);
  };

  const toggleRecording = async () => {
    if (recordState === "recording") {
      mediaRecorderRef.current?.stop();
      return;
    }
    // A browser with no recording support at all can't be talked into one — say
    // so plainly and hand them the text box instead of blaming permissions.
    if (
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined"
    ) {
      reportMicUnusable(
        "This browser can’t record audio — please type your answer instead.",
      );
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioChunksRef.current = [];
      const mimeType =
        [
          "audio/webm;codecs=opus",
          "audio/webm",
          "audio/ogg;codecs=opus",
          "audio/mp4",
        ].find((t) => MediaRecorder.isTypeSupported(t)) ?? "";
      const recorder = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const chunks = audioChunksRef.current;
        if (chunks.length === 0) {
          setRecordTranscribeError("No audio captured — try again.");
          setRecordState("idle");
          return;
        }
        setRecordState("transcribing");
        try {
          const blob = new Blob(chunks, { type: mimeType || "audio/webm" });
          const text = await transcribeAudio(blob);
          if (text.trim()) {
            setInput((prev) => (prev ? prev + " " + text : text));
            setShowTextInput(true);
            setTimeout(() => inputRef.current?.focus(), 50);
          } else {
            setRecordTranscribeError("Nothing was heard — try again.");
          }
        } catch (e) {
          console.error("Transcription failed", e);
          setRecordTranscribeError(
            "Transcription failed — try again or type your answer.",
          );
        } finally {
          setRecordState("idle");
        }
      };
      recorder.start(250); // 250ms timeslice ensures data flows reliably
      setRecordTranscribeError(null);
      setRecordState("recording");
    } catch (e) {
      console.error("Mic unavailable", e);
      const name = (e as DOMException)?.name ?? "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        // Recoverable, but only by the participant changing a setting — and the
        // browser won't re-prompt after a denial. Leave the orb up so a retry
        // works once they've flipped it.
        setRecordTranscribeError(
          "Microphone blocked. Allow it in your browser’s address bar, then " +
            "click the mic again — or type your answer instead.",
        );
      } else if (name === "NotFoundError" || name === "DevicesNotFoundError") {
        reportMicUnusable(
          "No microphone found — please type your answer instead.",
        );
      } else {
        reportMicUnusable(
          "The microphone wouldn’t start — please type your answer instead.",
        );
      }
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header with progress bar */}
      <div className="px-5 sm:px-8 pt-7 sm:pt-8 pb-5 shrink-0 w-full mx-auto max-w-[780px]">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400 mb-3">
          Before we begin
        </p>
        <div className="h-1.5 w-full rounded-full bg-slate-100 overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-indigo-400 to-violet-400 transition-all duration-500 ease-out"
            style={{ width: `${Math.round(progress * 100)}%` }}
          />
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col justify-center min-h-0 overflow-y-auto">
        <div className="px-5 sm:px-10 max-w-3xl mx-auto w-full">
          {/* Lead-in, then the questions. Same slot, same type treatment and the same word-by-word reveal,
              so the first question is a continuation of the voice that just spoke rather than a new screen.
              Advance-only: there is nothing to undo, and a Back here would just re-read a sentence. */}
          {inIntro ? (
            <div className="mb-9 sm:mb-12">
              <p className="text-[1.4rem] sm:text-[1.75rem] font-light text-slate-800 leading-[1.4] sm:leading-[1.45] tracking-[-0.015em]">
                <PopInText key={introStep} text={INTRO_SCREENS[introStep]} />
              </p>
              <div className="mt-9 flex items-center gap-4">
                <button
                  type="button"
                  onClick={() => {
                    if (introStep < INTRO_SCREENS.length - 1)
                      setIntroStep(introStep + 1);
                    else setInIntro(false);
                  }}
                  className="inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-sm shadow-indigo-200"
                >
                  {introStep < INTRO_SCREENS.length - 1 ? "Continue" : "Start"}
                </button>
                <span className="text-xs text-slate-400 font-mono">
                  {introStep + 1} / {INTRO_SCREENS.length}
                </span>
              </div>
            </div>
          ) : (
            <>
              {/* Question */}
              <div
                className="mb-9 sm:mb-12 transition-all duration-250"
                style={{
                  opacity: questionVisible ? 1 : 0,
                  transform: questionVisible
                    ? "translateY(0)"
                    : "translateY(10px)",
                }}
              >
                <p className="text-[1.4rem] sm:text-[1.75rem] font-light text-slate-800 leading-[1.4] sm:leading-[1.45] tracking-[-0.015em]">
                  <PopInText key={current.question} text={current.question} />
                </p>
              </div>

              {/* Mic orb */}
              {questionVisible && !showTextInput && (
                <div className="flex flex-col items-center py-4 mb-8 gap-3">
                  <MicOrb state={recordState} onToggle={toggleRecording} />
                </div>
              )}

              {/* Mic / transcription error — voice screen only: once they've switched
              to typing they've moved on, and a warning hanging above the answer
              they're writing is just noise. */}
              {questionVisible &&
                !showTextInput &&
                recordTranscribeError && (
                  <div
                    role="alert"
                    aria-live="assertive"
                    className="mb-6 mx-auto max-w-md flex gap-2.5 items-start rounded-xl border border-amber-200
                         bg-amber-50 px-4 py-3 text-left animate-fadeSlideIn"
                  >
                    <svg
                      className="w-4 h-4 text-amber-500 shrink-0 mt-px"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      <path d="M1 1l22 22" />
                      <path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6" />
                      <path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23M12 19v4" />
                    </svg>
                    <p className="text-xs text-amber-800 leading-relaxed">
                      {recordTranscribeError}
                    </p>
                  </div>
                )}

              {/* Text input */}
              {showTextInput && (
                <div className="space-y-3 mb-8">
                  <div className="flex gap-2 items-end">
                    <textarea
                      ref={inputRef}
                      rows={1}
                      className="flex-1 border border-slate-200 rounded-2xl px-5 py-3.5 text-sm text-slate-800 leading-[1.55]
                             placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-300
                             focus:border-transparent bg-white transition-colors duration-150 resize-none
                             max-h-60 overflow-y-auto"
                      placeholder={current.placeholder}
                      value={input}
                      onChange={(e) => {
                        setInput(e.target.value);
                        autosizeInput();
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          advance(input);
                        }
                      }}
                      disabled={recordState !== "idle"}
                      autoFocus={AUTOFOCUS_ANSWER}
                    />
                    <button
                      onClick={() => advance(input)}
                      disabled={
                        input.trim().length < current.minChars ||
                        recordState !== "idle"
                      }
                      className="w-10 h-10 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-30 text-white
                             rounded-xl flex items-center justify-center transition-all active:scale-95
                             shadow-sm shadow-indigo-200 shrink-0"
                    >
                      <svg
                        className="w-4 h-4"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <line x1="12" y1="19" x2="12" y2="5" />
                        <polyline points="5 12 12 5 19 12" />
                      </svg>
                    </button>
                  </div>
                  <p className="text-[11px] text-slate-400 pl-1">
                    <kbd className="bg-slate-100 px-1.5 py-0.5 rounded text-[10px] font-mono text-slate-500 mr-1">
                      Enter
                    </kbd>
                    to continue ·
                    <kbd className="bg-slate-100 px-1.5 py-0.5 rounded text-[10px] font-mono text-slate-500 mx-1">
                      Shift+Enter
                    </kbd>
                    for new line
                  </p>
                </div>
              )}

              {/* Type / speak toggle */}
              {questionVisible && (
                <div className="text-center">
                  {showTextInput ? (
                    <button
                      onClick={() => setShowTextInput(false)}
                      className="text-xs text-slate-400 hover:text-indigo-500 transition"
                    >
                      ← Prefer to speak?
                    </button>
                  ) : (
                    <button
                      onClick={() => {
                        setShowTextInput(true);
                        setTimeout(() => inputRef.current?.focus(), 50);
                      }}
                      className="text-xs text-slate-400 hover:text-indigo-500 transition"
                    >
                      Prefer to type? →
                    </button>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <div className="h-10 shrink-0" />
    </div>
  );
}
