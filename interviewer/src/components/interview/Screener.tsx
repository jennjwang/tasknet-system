import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useWorkflowStore } from "../../store";
import { screenerProbe } from "../../lib/api";
import { FADE_MS, QuestionCard, wait } from "./QuestionCard";
import { SCREENER_QUESTIONS, THIN_ANSWER_CHARS } from "../../settings";

// The pre-interview occupation screener (phase 'screen', only when SCREENER_TARGET_SOC is set). It runs on the
// interviewer's own question card — same mic orb, same type/speak toggle — rather than a separate form: two
// scripted questions, no planner calls, then the occupation selector, whose pick is the gate (pass → the
// interview, fail → screen-out).
//


export function Screener() {
  const { setPhase, addBackgroundTurn, setScreener } = useWorkflowStore(
    useShallow((s) => ({
      setPhase: s.setPhase,
      addBackgroundTurn: s.addBackgroundTurn,
      setScreener: s.setScreener,
    })),
  );

  // Which of the two scripted questions is showing, and the answers so far (kept
  // in a ref so the hand-off can't read a stale value).
  const [screenIdx, setScreenIdx] = useState(0);
  const screenAnswersRef = useRef<string[]>([]);
  // The one-shot substance follow-up. When the two answers aren't enough to
  // identify an occupation, this holds the extra question; once answered, its
  // text joins the responsibilities answer. Asked at most once — a participant
  // who still says nothing useful goes through to the selector rather than
  // getting stuck in a loop they can't satisfy.
  const [probeQ, setProbeQ] = useState<string | null>(null);
  const probeAskedRef = useRef(false);

  const [isEvaluating, setIsEvaluating] = useState(false);
  const [questionVisible, setQuestionVisible] = useState(true);
  // With the screener on, it owns the intro screens (consent → intro → screener → interview); the interview
  // skips them because a screener record means they've been seen.
  const [showIntro, setShowIntro] = useState(true);

  const question = probeQ ?? SCREENER_QUESTIONS[screenIdx].question;
  const placeholder = probeQ
    ? "One concrete example is plenty"
    : SCREENER_QUESTIONS[screenIdx].placeholder;

  // Record the answer as a planner turn (so the interview can seed from it), then
  // ask the next scripted question, or the substance follow-up, or hand off to
  // the occupation selector.
  const advance = async (answer: string) => {
    const wasProbe = probeQ !== null;
    addBackgroundTurn({
      field: "planner",
      question,
      answer,
      isFollowUp: wasProbe,
      timestamp: Date.now(),
    });
    // The follow-up's answer is more of the responsibilities answer, so it lands
    // in its own slot and is joined on below rather than overwriting anything.
    screenAnswersRef.current[wasProbe ? 2 : screenIdx] = answer;

    // Not the last scripted question yet — advance with the interview's fade.
    if (!wasProbe && screenIdx < SCREENER_QUESTIONS.length - 1) {
      setQuestionVisible(false);
      await wait(FADE_MS);
      setScreenIdx((i) => i + 1);
      setQuestionVisible(true);
      return;
    }

    const [title, resp, extra] = screenAnswersRef.current;
    const responsibilities = [resp, extra].filter(Boolean).join(" ");

    // Is there enough to identify an occupation? Only worth asking when the
    // answer is short — a long one is substantive by construction, and skipping
    // the call keeps the common case fast. Asked at most once.
    if (
      !wasProbe &&
      !probeAskedRef.current &&
      responsibilities.length < THIN_ANSWER_CHARS
    ) {
      probeAskedRef.current = true;
      setQuestionVisible(false);
      setIsEvaluating(true);
      const v = await screenerProbe({ title, responsibilities });
      if (!v.sufficient && v.followUp) {
        setProbeQ(v.followUp);
        setIsEvaluating(false);
        setQuestionVisible(true);
        return;
      }
    }

    // Hand off to the occupation selector, which proposes candidates from these
    // answers and gates on what the participant picks.
    setScreener({ title, responsibilities, at: Date.now() });
    setQuestionVisible(false);
    setIsEvaluating(true);
    setPhase("occupation-select");
  };

  // RESUME across a reload: replay the saved answers and re-ask from wherever they
  // stopped. (Only reachable mid-screener — a pass advances the phase, and a fail
  // is pinned by the server-side screen-out record.)
  const didResumeRef = useRef(false);
  useEffect(() => {
    if (didResumeRef.current) return;
    didResumeRef.current = true;
    const prior = useWorkflowStore
      .getState()
      .backgroundTranscript.filter(
        (t) => t.field === "planner" && t.answer?.trim(),
      );
    if (prior.length === 0) return;
    screenAnswersRef.current = prior
      .slice(0, SCREENER_QUESTIONS.length)
      .map((t) => t.answer);
    setScreenIdx(
      Math.min(
        screenAnswersRef.current.length,
        SCREENER_QUESTIONS.length - 1,
      ),
    );
    setShowIntro(false); // already read it before answering
  }, []);

  return (
    <QuestionCard
      heading="Before we begin"
      // Its own 0→1 bar over the two questions: a separate, known-length phase, and
      // finishing it is a milestone worth showing complete. The interview's bar then
      // starts over. The follow-up holds the bar where it is: it is an extra ask on
      // the SAME question, not a third question.
      progress={screenIdx / SCREENER_QUESTIONS.length}
      intro={showIntro}
      onIntroDone={() => setShowIntro(false)}
      question={question}
      placeholder={placeholder}
      questionVisible={questionVisible}
      isEvaluating={isEvaluating}
      slowLabels={["One moment…", "One moment…"]}
      onSubmit={advance}
    />
  );
}
