// Shared chrome for the validation survey's screens.

export const Frame = ({
  children,
  full = false,
}: {
  children: React.ReactNode;
  full?: boolean; // full-bleed: skip the centered max-width column (e.g. sidebar layouts)
}) => (
  <div className="relative flex app-screen w-screen overflow-hidden bg-white">
    <div className="absolute top-0 left-0 right-0 h-48 bg-gradient-to-b from-indigo-50/40 to-transparent pointer-events-none" />
    <div
      className={`relative h-full ${full ? "w-full" : "w-full max-w-[1040px] mx-auto"}`}
    >
      {children}
    </div>
  </div>
);

export const Centered = ({ children }: { children: React.ReactNode }) => (
  <div className="flex h-full items-center justify-center px-6 text-center">
    <div className="max-w-lg animate-fadeSlideIn">{children}</div>
  </div>
);

// The arrow on the screens' Continue buttons.
export const ArrowRight = () => (
  <svg
    className="w-4 h-4"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <line x1="5" y1="12" x2="19" y2="12" />
    <polyline points="13 6 19 12 13 18" />
  </svg>
);

export function resolveExternalId(): string {
  const q = new URLSearchParams(window.location.search);
  // The participant id comes from the link (?id=, as the interview app uses, or ?externalId= / ?pid=).
  // Without one, an anonymous id is minted into localStorage, which can't resume on another device.
  const raw = q.get("externalId") || q.get("pid") || q.get("id") || "";
  const clean = raw.replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 64);
  if (clean) return clean;
  // Persisted in localStorage so a refresh keeps the same identity (sticky assignment).
  const KEY = "validation_anon_id";
  try {
    let id = window.localStorage.getItem(KEY) || "";
    if (!id) {
      id = `anon-${Math.random().toString(36).slice(2, 10)}`;
      window.localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return `anon-${Math.random().toString(36).slice(2, 10)}`;
  }
}
