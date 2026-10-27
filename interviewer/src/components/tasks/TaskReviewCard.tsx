import { useRef, useState } from "react";
import { TaskItem } from "../../types";
import { PencilIcon } from "../ui";

// One task at a time: "I do this" / "I don't", with an inline edit of the wording.
interface TaskReviewCardProps {
  task: TaskItem;
  taskIdx: number;
  onSaveEdit: (idx: number, name: string) => void;
  onAdvance: (answer: "yes" | "no") => void;
}

export function TaskReviewCard({
  task,
  taskIdx,
  onSaveEdit,
  onAdvance,
}: TaskReviewCardProps) {
  const [primaryAnswer, setPrimaryAnswer] = useState<"yes" | "no" | null>(null);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(task.name);
  // Buttons are locked on card 1 until the participant clicks the pencil; the
  // coach mark shows until then.
  const [pencilClicked, setPencilClicked] = useState(taskIdx !== 0);
  const showCoachMark = !pencilClicked;
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const startEdit = () => {
    setEditValue(task.name);
    setEditing(true);
    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.style.height = "auto";
        inputRef.current.style.height = inputRef.current.scrollHeight + "px";
        inputRef.current.focus();
        // Place the caret at the end rather than selecting all — selecting all
        // means the first keystroke wipes the statement, which makes small
        // tweaks awkward. End-caret lets the participant edit in place.
        const end = inputRef.current.value.length;
        inputRef.current.setSelectionRange(end, end);
      }
    }, 20);
  };

  const commitEdit = () => {
    setEditing(false);
    const trimmed = editValue.trim() || task.name;
    onSaveEdit(taskIdx, trimmed);
  };

  return (
    <div className="h-full relative">
      {/* Task name + buttons are vertically centered; the big bottom padding
          keeps the centering point where it was when a Continue button was
          pinned below. */}
      <div className="h-full flex flex-col justify-center gap-6 sm:gap-7 pb-24 sm:pb-48">
        {/* Task name */}
        <div className="pb-1">
          {editing ? (
            <textarea
              ref={inputRef}
              className="w-full text-xl sm:text-2xl font-light text-slate-800 bg-transparent border-b-2 border-indigo-300 focus:outline-none pb-1 resize-none overflow-hidden leading-snug"
              value={editValue}
              rows={1}
              onChange={(e) => {
                setEditValue(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = e.target.scrollHeight + "px";
              }}
              onBlur={commitEdit}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitEdit();
                }
                if (e.key === "Escape") {
                  setEditing(false);
                  setEditValue(task.name);
                }
              }}
            />
          ) : (
            <div className="flex items-start gap-3">
              <p className="text-xl sm:text-2xl font-light text-slate-800 leading-snug flex-1">
                {task.name}
              </p>
              {/* Pencil button — only edit trigger */}
              <div className="relative mt-1 shrink-0">
                {showCoachMark && (
                  <span className="absolute inset-0 rounded-full bg-indigo-400 opacity-30 animate-ping" />
                )}
                <button
                  type="button"
                  onClick={() => {
                    setPencilClicked(true);
                    startEdit();
                  }}
                  className="relative flex items-center justify-center w-8 h-8 rounded-full bg-slate-100 hover:bg-indigo-50 hover:text-indigo-600 text-slate-500 transition-colors"
                  aria-label="Edit task"
                >
                  <PencilIcon className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Coach mark callout — first card only, blocks buttons until pencil clicked */}
        {showCoachMark && (
          <div className="animate-popIn flex items-start gap-2.5 px-4 py-3 rounded-xl bg-indigo-50 border border-indigo-200 -mt-2">
            <PencilIcon className="w-3.5 h-3.5 mt-0.5 shrink-0 text-indigo-400" />
            <p className="text-sm text-indigo-700 leading-relaxed">
              <span className="font-semibold">
                Use the pencil to edit the statement.
              </span>{" "}
              Add any specifics, remove what doesn't fit, or rewrite it in your
              own words.
            </p>
          </div>
        )}

        {/* Answer control */}
          <div className="flex gap-3">
            {(
              [
                {
                  value: "yes",
                  label: "I do this",
                  active:
                    "bg-indigo-600 border-indigo-600 text-white shadow-sm shadow-indigo-200",
                  inactive: "hover:border-indigo-200 hover:text-indigo-600",
                },
                {
                  value: "no",
                  label: "I don't ever do this",
                  active: "bg-slate-100 border-slate-300 text-slate-700",
                  inactive: "hover:border-slate-300",
                },
              ] as {
                value: "yes" | "no";
                label: string;
                active: string;
                inactive: string;
              }[]
            ).map(({ value, label, active, inactive }) => (
              <button
                key={value}
                disabled={!pencilClicked}
                onClick={() => {
                  setPrimaryAnswer(value);
                  setTimeout(() => onAdvance(value), 220);
                }}
                className={`flex-1 py-3 rounded-2xl border text-sm font-medium transition-all active:scale-[0.98] disabled:opacity-30 disabled:cursor-not-allowed ${
                  primaryAnswer === value
                    ? active
                    : `bg-white border-slate-200 text-slate-600 ${inactive}`
                }`}
              >
                {label}
              </button>
            ))}
          </div>
      </div>

    </div>
  );
}
