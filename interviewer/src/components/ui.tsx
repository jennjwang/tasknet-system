// Small presentational pieces shared by several screens.

// Three bouncing dots, the app's loading indicator. `dot` sizes and colours each dot.
export function LoadingDots({ dot, gap = "gap-2" }: { dot: string; gap?: string }) {
  return (
    <div className={`flex ${gap}`}>
      {[0, 150, 300].map((d) => (
        <span
          key={d}
          className={`${dot} rounded-full animate-bounce`}
          style={{ animationDelay: `${d}ms` }}
        />
      ))}
    </div>
  );
}

export function ArrowRightIcon({
  strokeWidth = 2.5,
  head = "12 5 19 12 12 19",
}: {
  strokeWidth?: number;
  head?: string;
}) {
  return (
    <svg
      className="w-4 h-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <line x1="5" y1="12" x2="19" y2="12" />
      <polyline points={head} />
    </svg>
  );
}

export function PencilIcon({ className }: { className: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M11 2l3 3-8 8H3v-3l8-8z" />
    </svg>
  );
}

export function MicIcon({ className }: { className: string }) {
  const stroke = { stroke: "currentColor", strokeWidth: "1.8", strokeLinecap: "round" as const };
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <rect x="9" y="2" width="6" height="12" rx="3" fill="currentColor" />
      <path d="M5 10a7 7 0 0 0 14 0" {...stroke} fill="none" />
      <line x1="12" y1="19" x2="12" y2="22" {...stroke} />
      <line x1="9" y1="22" x2="15" y2="22" {...stroke} />
    </svg>
  );
}

export function StopIcon({ className }: { className: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}
