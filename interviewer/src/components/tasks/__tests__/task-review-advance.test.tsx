/**
 * A double-click on a review card must not skip the next card.
 *
 * THE BUG THIS EXISTS FOR (fixed 2026-09-20). The answer buttons call onAdvance from a
 * setTimeout(220ms), so two clicks inside that window queue two advance() calls sharing ONE render
 * closure. Both wrote to card k, while the pointer moved with setCurrentIdx(i => i + 1) -- twice. Card
 * k+1 rendered long enough to stamp `shownAt` and was then jumped: saved as shown-but-never-answered and
 * dropped at ingest. It cost 8 cards across 3 sessions before anyone noticed, because a skipped card
 * leaves no error, only a gap.
 *
 * WHY A COMPONENT TEST AND NOT A UNIT TEST ON THE REDUCER. The bug was not in the arithmetic; every
 * individual update was correct in isolation. It was in React's semantics -- a handler closing over a
 * stale index while a functional update read the fresh one -- so only mounting the real component and
 * dispatching two real clicks can fail when it regresses.
 *
 * The deck is a fixed task list streamed by a stubbed generator, with Math.random pinned so the spliced
 * attention check (answered "I don't") is the same on every mount.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act, cleanup } from "@testing-library/react";
import { TaskSelection } from "../TaskSelection";
import { useWorkflowStore } from "../../../store";
import { ATTENTION_CHECKS } from "../../../settings";

// The generator streams this deck; nothing else may touch the network.
const DECK = vi.hoisted(() => [
  "Build and ship UI components",
  "Review pull requests",
  "Debug production issues",
  "Write integration tests",
  "Sync with design",
  "Plan the sprint and groom the backlog",
  "Pair with teammates on tricky features",
  "Update technical documentation",
  "Investigate and triage bug reports",
  "Mentor junior engineers",
]);
vi.mock("../../../lib/api", () => ({
  generateTasksFromInterview: vi.fn(async (_job: string, _tasks: string[], onTask: (n: string) => void) => {
    for (const n of DECK) onTask(n);
  }),
  extractInterviewTasks: vi.fn(async () => []),
  recordScreenOut: vi.fn(async () => {}),
  transcribeAudio: vi.fn(async () => ""),
}));

/** Answer the visible card, firing `clicks` times inside the 220ms advance timer. */
function answerCard(clicks: number) {
  // The first card gates its answers behind the edit-pencil coach mark.
  const yes = screen.getByRole("button", { name: /i do this/i }) as HTMLButtonElement;
  if (yes.disabled) {
    const pencil = document.querySelector<HTMLButtonElement>("button[aria-label*='dit'], button[title*='dit']");
    if (pencil) fireEvent.click(pencil);
  }
  // The attention check is answered "I don't", as an honest participant would.
  const isCheck = ATTENTION_CHECKS.includes(currentCard() ?? "");
  const btn = screen.getByRole("button", { name: isCheck ? /i don't ever do this/i : /i do this/i }) as HTMLButtonElement;
  for (let i = 0; i < clicks; i++) fireEvent.click(btn);   // same tick: one render closure for all of them
  act(() => { vi.advanceTimersByTime(400); });             // let the queued advance timers fire
}

/** The statement on the card, or null once the deck is exhausted. */
const currentCard = () =>
  document.querySelector("p.text-xl")?.textContent?.trim() ?? null;

/** Dismiss the intro screen the deck opens on. */
function enterDeck() {
  const start = screen.queryByRole("button", { name: /^start$/i });
  if (start) fireEvent.click(start);
}

function walkDeck(clicksPerCard: number): string[] {
  enterDeck();
  const seen: string[] = [];
  for (let i = 0; i < 40; i++) {
    const name = currentCard();
    if (!name || !screen.queryByRole("button", { name: /i do this/i })) break;
    seen.push(name);
    answerCard(clicksPerCard);
  }
  return seen;
}

describe("task review card advance", () => {
  beforeEach(() => {
    cleanup();
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    useWorkflowStore.setState({ phase: "task-selection", taskItems: [], selectedTasks: [] } as never, false);
  });

  /** Mount a fresh copy of the deck, let it load, and walk it, `clicks` clicks per card. */
  async function walkFresh(clicks: number): Promise<string[]> {
    cleanup();
    window.history.replaceState({}, "", `/?id=test-advance-${clicks}`);
    render(<TaskSelection />);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });   // the load's 1.2s minimum display
    return walkDeck(clicks);
  }

  it("shows every card in the deck, each exactly once", async () => {
    const seen = await walkFresh(1);
    expect(seen.length).toBe(DECK.length + 1);   // every task plus the attention check
    expect(new Set(seen).size).toBe(seen.length);
  });

  // The deck is the same every mount, so the sequence of cards reached is the invariant: any extra
  // click that moves the pointer shortens it. Comparing against the single-click walk is what makes
  // these fail on the pre-fix component -- a bare "no duplicates" assertion passes even when the
  // pointer jumps by five, because the two cards it does reach are still distinct.
  it("does not skip a card when the answer is double-clicked", async () => {
    expect(await walkFresh(2)).toEqual(await walkFresh(1));
  });

  it("does not skip a card under a burst of clicks", async () => {
    expect(await walkFresh(5)).toEqual(await walkFresh(1));
  });
});
