import { Fragment, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useWorkflowStore } from "../../store";
import { transcribeAudio } from "../../lib/api";
import { introScreens } from "../../settings";
import { RecordState, startRecording } from "../../lib/recorder";
import { ArrowRightIcon, LoadingDots, MicIcon, StopIcon } from "../ui";

// The question card the interview and the screener both run on: header and progress bar, the intro screens, the
// current question, voice-first answers (mic orb, "Prefer to type?" toggle, auto-growing text box), and the
// waiting label while an answer is processed. The caller owns the flow: which question shows, when it fades
// (questionVisible), and what an answer does (onSubmit).

// The fade between one question and the next.
export const FADE_MS = 260;
export const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));


// Varied first-stage acknowledgements for the inter-question loader, so it
// doesn't always read the same. One is picked at random each time.
const ACK_MESSAGES = [
  "Got it",
  "Got that",
  "Thanks!",
  "Makes sense",
  "Good to know",
  "Noted",
  "Perfect",
  "Great, thanks",
];

// Split copy into animatable words, marking the ones inside **…** as bold. Each
// word is its own animated span, so emphasis can't be a markup blob in the
// string — it has to survive the word split.
function popInWords(text: string): { word: string; bold: boolean }[] {
  return text
    .split("**")
    .flatMap((segment, i) =>
      segment
        .split(" ")
        .filter(Boolean)
        .map((word) => ({ word, bold: i % 2 === 1 })),
    );
}

// Reveals text one word at a time, each word rising and fading in (staggered).
// Calls onDone once the last word has finished animating.
const POP_START = 30;
const POP_STAGGER = 58;
const POP_FADE = 600;
function PopInText({
  text,
  onDone,
  stagger = POP_STAGGER,
  fade = POP_FADE,
}: {
  text: string;
  onDone?: () => void;
  stagger?: number;
  fade?: number;
}) {
  const [shown, setShown] = useState(false);
  const words = useMemo(() => popInWords(text), [text]);
  useEffect(() => {
    const start = setTimeout(() => setShown(true), POP_START);
    const wordCount = words.length;
    const total = POP_START + (wordCount - 1) * stagger + fade;
    const done = onDone ? setTimeout(onDone, total) : undefined;
    return () => {
      clearTimeout(start);
      if (done) clearTimeout(done);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <>
      {words.map(({ word, bold }, i) => (
        <Fragment key={i}>
          <span
            className={`inline-block${bold ? " font-semibold text-slate-900" : ""}`}
            style={{
              opacity: shown ? 1 : 0,
              transform: shown ? "translateY(0)" : "translateY(10px)",
              transition: `opacity ${fade}ms ease, transform ${fade}ms ease`,
              transitionDelay: `${i * stagger}ms`,
            }}
          >
            {word}
          </span>{" "}
        </Fragment>
      ))}
    </>
  );
}

// The intro/outro Continue button, faded in once `ready` (after the copy above it
// has popped in). `children` render below the button.
function ReadyButton({
  ready,
  onClick,
  label,
  className = "",
  children,
}: {
  ready: boolean;
  onClick: () => void;
  label: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div
      className={`mt-10 ${className} transition-all duration-500`}
      style={{
        opacity: ready ? 1 : 0,
        transform: ready ? "translateY(0)" : "translateY(8px)",
        pointerEvents: ready ? "auto" : "none",
      }}
    >
      <button
        onClick={onClick}
        className="inline-flex items-center gap-2 px-6 py-3 bg-indigo-600 hover:bg-indigo-700
                   text-white text-sm font-medium rounded-xl transition-all active:scale-95
                   shadow-sm shadow-indigo-200"
      >
        {label}
        <ArrowRightIcon />
      </button>
      {children}
    </div>
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
            <StopIcon className="w-6 h-6 text-white" />
          ) : (
            <MicIcon className="w-8 h-8 text-indigo-600" />
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

// Desktop (mouse/trackpad) benefits from auto-focusing the answer box so the
// participant can just start typing. On touch devices, auto-focus instead pops
// the on-screen keyboard and scrolls the viewport on every question transition
// (the textarea unmounts during evaluation and remounts for the next question),
// which feels jarring. Gate auto-focus to fine pointers so mobile users open
// the keyboard by tapping the field themselves. User-initiated focuses (the
// "Prefer to type?" toggle, post-transcription edit) are intentionally left on.
const AUTOFOCUS_ANSWER =
  typeof window !== "undefined" &&
  !!window.matchMedia &&
  window.matchMedia("(pointer: fine)").matches;

export function QuestionCard({
  heading,
  progress,
  intro,
  onIntroDone,
  question,
  placeholder,
  questionVisible,
  isEvaluating,
  slowLabels,
  onSubmit,
  outro,
  error,
}: {
  /** The small label above the progress bar. */
  heading: string;
  /** 0–1. */
  progress: number;
  /** Show the intro screens; onIntroDone fires after the last one. */
  intro: boolean;
  onIntroDone: () => void;
  question: string;
  placeholder: string;
  questionVisible: boolean;
  isEvaluating: boolean;
  /** The waiting label after ~4s and ~7.5s (the first few seconds show a short acknowledgement). */
  slowLabels: [string, string];
  /** Called with the trimmed answer; the card has already cleared the input. */
  onSubmit: (answer: string) => void | Promise<void>;
  /** Replaces the question with closing copy and a Continue button. */
  outro?: { text: string; onContinue: () => void } | null;
  /** Replaces the question with an apology and a Retry button. */
  error?: { onRetry: () => void } | null;
}) {
  const invitedTitle = useWorkflowStore((s) => s.invitedTitle);
  // Recomputed if the ?soc= title lands after mount rather than pinned on it:
  // the nudge sits on the SECOND screen, so a lookup still in flight when the
  // card mounts has resolved well before anyone reads it, and pinning would
  // silently drop the nudge for a participant who clicked through consent fast.
  const INTRO_SCREENS = useMemo(() => introScreens(invitedTitle), [invitedTitle]);
  const [introStep, setIntroStep] = useState(0);
  // Continue buttons appear only once the words finish popping in.
  const [introButtonReady, setIntroButtonReady] = useState(false);
  const [outroButtonReady, setOutroButtonReady] = useState(false);

  const advanceIntro = () => {
    setIntroButtonReady(false);
    if (introStep < INTRO_SCREENS.length - 1) {
      setIntroStep((s) => s + 1);
    } else {
      onIntroDone();
    }
  };

  const [input, setInput] = useState("");
  const [showTextInput, setShowTextInput] = useState(false);
  const [recordState, setRecordState] = useState<RecordState>("idle");
  const [recordTranscribeError, setRecordTranscribeError] = useState<
    string | null
  >(null);
  // Progresses the inter-question loading label so a longer wait (several LLM
  // calls per turn) reads as steady progress rather than a frozen screen. Resets
  // whenever evaluation starts/ends.
  const [evalStage, setEvalStage] = useState(0);
  // A varied acknowledgement, picked fresh each time so it doesn't feel canned.
  const [ackMsg, setAckMsg] = useState("Got it");

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  // Set once recording turns out to be impossible in this browser (no
  // MediaRecorder, no input device). Every later question then opens in typing
  // mode instead of voice-first, so the participant isn't re-clicking "Prefer to
  // type?" on every turn — a participant who hit this reported it as the reason
  // they cut their answers short.
  const micUnavailableRef = useRef(false);

  /** Report a mic failure the participant can't fix by changing a setting. Stays on the
      voice screen so the message is actually readable — revealing the text box here would
      hide it, since the warning is deliberately voice-screen-only. "Prefer to type?" is
      directly below it, and every LATER question opens in typing mode by itself. */
  const reportMicUnusable = (message: string) => {
    micUnavailableRef.current = true;
    setRecordTranscribeError(message);
    setRecordState("idle");
  };

  // Size the auto-grow textarea to its content — or, when empty, to its
  // (possibly wrapping) placeholder, so a multi-line placeholder is never
  // clipped on narrow/mobile widths where the hint text wraps. Reads the
  // placeholder straight off the DOM, so it stays one line on wider screens
  // where it fits without forcing a fixed taller box.
  const autosizeInput = () => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    // scrollHeight is content+padding only; with box-border the set height must
    // add the border so the content area isn't clipped by the border width.
    const borderY = el.offsetHeight - el.clientHeight;
    const fit = (content: number) => `${content + borderY}px`;
    if (el.value) {
      el.style.height = fit(el.scrollHeight);
    } else {
      const ph = el.placeholder;
      el.value = ph;
      el.style.height = fit(el.scrollHeight);
      el.value = "";
    }
  };

  // Advance the loading label while an answer is being processed: a quick
  // acknowledgement first, then the caller's slower labels if the wait runs long.
  // Resets the moment evaluation ends.
  useEffect(() => {
    if (!isEvaluating) {
      setEvalStage(0);
      return;
    }
    setAckMsg(ACK_MESSAGES[Math.floor(Math.random() * ACK_MESSAGES.length)]);
    const t1 = setTimeout(() => setEvalStage(1), 4000);
    const t2 = setTimeout(() => setEvalStage(2), 7500);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [isEvaluating]);

  // Re-size the input whenever its content, its placeholder (question/follow-up
  // change), or its visibility (voice↔text toggle, mount) changes.
  useEffect(() => {
    autosizeInput();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, placeholder, showTextInput]);

  // Nothing typed carries into the outro.
  const showOutro = !!outro;
  useEffect(() => {
    if (!showOutro) return;
    setInput("");
    setShowTextInput(false);
  }, [showOutro]);

  const advance = async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed || isEvaluating) return;

    setInput("");
    // Back to voice-first for the next question — unless recording is impossible
    // here, in which case stay in typing mode rather than making them re-open it.
    setShowTextInput(micUnavailableRef.current);
    // Clear any stale transcription error so it never carries into the next
    // question / follow-up.
    setRecordTranscribeError(null);

    await onSubmit(trimmed);
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
      mediaRecorderRef.current = await startRecording(async (blob) => {
        if (!blob) {
          setRecordTranscribeError("No audio captured — try again.");
          setRecordState("idle");
          return;
        }
        setRecordState("transcribing");
        try {
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
      });
      setRecordTranscribeError(null);
      setRecordState("recording");
    } catch (e) {
      console.error("Mic unavailable", e);
      const name = (e as DOMException)?.name ?? "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        // Recoverable, but only by the participant changing a setting — and the
        // browser won't re-prompt after a denial, so clicking again does nothing
        // until they do. Name where the switch is, and leave the orb up (no
        // reportMicUnusable) so a retry works once they've flipped it.
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

  // The answer controls show only on a question: not on the intro, the outro or the error.
  const answering = !intro && !showOutro && !error;

  return (
    <div className="flex flex-col h-full bg-white relative overflow-hidden">
      <div className="absolute top-0 left-0 right-0 h-56 bg-gradient-to-b from-indigo-50/30 to-transparent pointer-events-none" />

      {/* Header with progress bar */}
      <div className="relative z-10 px-5 sm:px-8 pt-7 sm:pt-8 pb-5 shrink-0 w-full mx-auto max-w-[780px]">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400 mb-3">
          {heading}
        </p>
        <div className="h-1.5 w-full rounded-full bg-slate-100 overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-indigo-400 to-violet-400 transition-all duration-500 ease-out"
            style={{ width: `${Math.round(progress * 100)}%` }}
          />
        </div>
      </div>

      {/* Main content */}
      <div className="relative z-10 flex-1 flex flex-col justify-center min-h-0 overflow-y-auto">
        <div className="px-5 sm:px-10 max-w-3xl mx-auto w-full">
          {/* Headline slot — intro copy or the current question (shared fade) */}
          <div
            className="mb-9 sm:mb-12 transition-all duration-250"
            style={{
              opacity: questionVisible ? 1 : 0,
              transform: questionVisible ? "translateY(0)" : "translateY(10px)",
            }}
          >
            {intro ? (
              <p className="text-[1.3rem] sm:text-[1.6rem] font-light text-slate-800 leading-[1.55] tracking-[-0.01em]">
                <PopInText
                  key={`intro-${introStep}`}
                  text={INTRO_SCREENS[introStep]}
                  stagger={38}
                  fade={460}
                  onDone={() => setIntroButtonReady(true)}
                />
              </p>
            ) : outro ? (
              <p className="text-[1.3rem] sm:text-[1.6rem] font-light text-slate-800 leading-[1.55] tracking-[-0.01em]">
                <PopInText
                  key="outro"
                  text={outro.text}
                  onDone={() => setOutroButtonReady(true)}
                />
              </p>
            ) : error ? (
              <div role="alert" className="animate-fadeSlideIn">
                <p className="text-[1.15rem] sm:text-[1.35rem] font-light text-slate-800 leading-[1.5]">
                  Something went wrong on our side. Please wait a moment and try again.
                </p>
                <button
                  onClick={error.onRetry}
                  className="mt-6 inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white
                             text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-sm shadow-indigo-200"
                >
                  Try again
                </button>
              </div>
            ) : (
              <p className="text-[1.4rem] sm:text-[1.75rem] font-light text-slate-800 leading-[1.4] sm:leading-[1.45] tracking-[-0.015em]">
                <PopInText
                  key={question}
                  text={question}
                  stagger={38}
                  fade={460}
                />
              </p>
            )}
          </div>

          {/* Action slot — Continue (intro), with a dot per intro screen */}
          {intro && (
            <ReadyButton
              ready={introButtonReady}
              onClick={advanceIntro}
              label={introStep < INTRO_SCREENS.length - 1 ? "Next" : "Begin"}
              className="flex flex-col items-start gap-5"
            >
              <div className="flex gap-1.5">
                {INTRO_SCREENS.map((_, i) => (
                  <span
                    key={i}
                    className={`h-1.5 rounded-full transition-all duration-300 ${
                      i === introStep ? "w-5 bg-indigo-400" : "w-1.5 bg-slate-200"
                    }`}
                  />
                ))}
              </div>
            </ReadyButton>
          )}

          {/* Action slot — Continue (outro) */}
          {outro && (
            <ReadyButton ready={outroButtonReady} onClick={outro.onContinue} label="Continue" />
          )}

          {/* Evaluating indicator — only mid-interview (while a submitted answer
              is being processed), never on the outro where Continue is shown. */}
          {isEvaluating && !showOutro && (
            <div className="flex items-center gap-2.5 mb-8 text-sm text-slate-400">
              <LoadingDots dot="w-1.5 h-1.5 bg-indigo-300" gap="gap-1" />
              <span key={evalStage} className="animate-fadeSlideIn">
                {evalStage === 0 ? ackMsg : slowLabels[evalStage - 1]}
              </span>
            </div>
          )}

          {/* Mic orb */}
          {answering && questionVisible && !showTextInput && !isEvaluating && (
            <div className="flex flex-col items-center py-4 mb-8 gap-3">
              <MicOrb state={recordState} onToggle={toggleRecording} />
            </div>
          )}

          {/* Mic / transcription error — only ever on the voice screen, never over the
              text box: once they've switched to typing they've moved on, and a warning
              hanging above the answer they're writing is just noise. Kept out of the orb
              block above so the orb's layout doesn't shift, but gated on the same
              !showTextInput. */}
          {answering &&
            questionVisible &&
            !showTextInput &&
            !isEvaluating &&
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
          {answering && showTextInput && !isEvaluating && (
            <div className="space-y-3 mb-8">
              <div className="flex gap-2 items-end">
                <textarea
                  ref={inputRef}
                  rows={1}
                  className="flex-1 border border-slate-200 rounded-2xl px-5 py-3.5 text-sm text-slate-800 leading-[1.55]
                             placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-300
                             focus:border-transparent bg-white transition-colors duration-150 resize-none
                             max-h-60 overflow-y-auto"
                  placeholder={placeholder}
                  value={input}
                  onChange={(e) => {
                    setInput(e.target.value);
                    // Auto-grow so the participant can see everything they've typed.
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
                    !input.trim() || recordState !== "idle"
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
          {answering && questionVisible && !isEvaluating && (
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
        </div>
      </div>

      <div className="h-10 shrink-0" />
    </div>
  );
}
