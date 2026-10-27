/**
 * Task review: loading, Retry, the "Done" count, and the screen-out save.
 *
 * Each case here was a participant-visible bug:
 *   - Retry on the load-error screen only reset the loading flag and never re-ran the load, so the
 *     participant watched a spinner forever.
 *   - "Done — continue with N tasks" counted an attention check answered "yes" (reachable when
 *     ATTN_CHECK_MAX_FAILS > 0), though finalize() never carries a check into selectedTasks.
 *   - ScreenOut saved a hand-picked subset without externalId, so the server wrote it to
 *     <sessionId>.json instead of <externalId>_<sessionId>.json, detached from the participant.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act, cleanup } from "@testing-library/react";
import { TaskSelection } from "../TaskSelection";
import { ScreenOut } from "../../ScreenOut";
import { useWorkflowStore } from "../../../store";
import * as api from "../../../lib/api";

vi.mock("../../../lib/api", () => ({
  generateTasksFromInterview: vi.fn(),
  extractInterviewTasks: vi.fn(async () => []),
  recordScreenOut: vi.fn(async () => {}),
  transcribeAudio: vi.fn(async () => ""),
  saveSession: vi.fn(async () => {}),
}));

const generate = vi.mocked(api.generateTasksFromInterview);

/** A generator stub that streams `names` through onTask (the third argument). */
const streams = (names: string[]) =>
  (async (_job: string, _tasks: string[], onTask: (n: string) => void) => {
    for (const n of names) onTask(n);
  }) as typeof api.generateTasksFromInterview;

/** Let pending promises and the load's 1.2s minimum-display timer run. */
async function flushLoad() {
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
}

const currentCard = () => document.querySelector("p.text-xl")?.textContent?.trim() ?? null;

function clickStart() {
  fireEvent.click(screen.getByRole("button", { name: /^start$/i }));
}

/** Answer the visible card "I do this" (opening the pencil first on card 1, which gates the answers). */
function answerYes() {
  let yes = screen.getByRole("button", { name: /i do this/i }) as HTMLButtonElement;
  if (yes.disabled) {
    fireEvent.click(screen.getByRole("button", { name: /edit task/i }));
    // Opening the editor swaps the card into edit mode; commit it unchanged.
    const save = screen.queryByRole("button", { name: /^(save|done)$/i });
    if (save) fireEvent.click(save);
    yes = screen.getByRole("button", { name: /i do this/i }) as HTMLButtonElement;
  }
  fireEvent.click(yes);
  act(() => { vi.advanceTimersByTime(400); });
}

beforeEach(() => {
  cleanup();
  vi.useFakeTimers();
  generate.mockReset();
  vi.mocked(api.saveSession).mockClear();
  window.history.replaceState({}, "", "/");
  useWorkflowStore.setState({ phase: "task-selection", taskItems: [], selectedTasks: [] } as never, false);
});

describe("task load", () => {
  it("Retry re-runs the load after a failure", async () => {
    generate
      .mockImplementationOnce(async () => { throw new Error("upstream 500"); })
      .mockImplementationOnce(streams(["Task one", "Task two"]));

    render(<TaskSelection />);
    await flushLoad();
    clickStart();
    expect(screen.getByText(/something went wrong on our side/i)).toBeTruthy();
    expect(generate).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await flushLoad();

    expect(generate).toHaveBeenCalledTimes(2);
    // Straight back to the deck -- the intro was already read -- starting at the first card.
    expect(screen.queryByRole("button", { name: /^start$/i })).toBeNull();
    expect(currentCard()).toBe("Task one");
  });
});

describe("Done count", () => {
  it("counts only real tasks, not an attention check answered yes", async () => {
    // Tolerate attention-check fails so a "yes" on the check does not screen the participant out.
    useWorkflowStore.setState({
      screening: { attnCheckMaxFails: 5, attnCheckFails: 0, screenedOut: false, screenOutReason: null },
    } as never, false);
    const real = Array.from({ length: 16 }, (_, i) => `Real task ${i + 1}`);
    generate.mockImplementationOnce(streams(real));

    render(<TaskSelection />);
    await flushLoad();
    clickStart();

    // Answer "yes" to the first 15 cards (DONE_THRESHOLD), which unlocks the Done button.
    const answered: string[] = [];
    for (let i = 0; i < 15; i++) {
      answered.push(currentCard()!);
      answerYes();
    }
    const realAnswered = answered.filter((n) => real.includes(n)).length;
    expect(realAnswered).toBe(14);   // one of the 15 was the spliced attention check
    const done = screen.getByRole("button", { name: /done — continue with/i });
    expect(done.textContent).toMatch(new RegExp(`continue with ${realAnswered} tasks`));
  });
});

describe("ScreenOut save", () => {
  it("carries externalId so the server files it under <externalId>_<sessionId>.json", () => {
    useWorkflowStore.setState({
      externalId: "p-123",
      screening: { attnCheckMaxFails: 0, attnCheckFails: 1, screenedOut: true, screenOutReason: "attention-check-failed" },
    } as never, false);
    render(<ScreenOut />);
    const save = vi.mocked(api.saveSession);
    expect(save).toHaveBeenCalledTimes(1);
    const [sessionId, payload] = save.mock.calls[0];
    expect(sessionId).toBe(useWorkflowStore.getState().sessionId);
    expect(payload).toMatchObject({
      externalId: "p-123",
      screenedOut: true,
      screenOutReason: "attention-check-failed",
    });
  });
});
