import clsx from "clsx";
import type { ReactNode } from "react";

/** Semantic status tone. Colour on this screen always means an operational state. */
export type Tone = "neutral" | "signal" | "breach" | "secure" | "caution";

const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-mist",
  signal: "text-signal",
  breach: "text-breach",
  secure: "text-secure",
  caution: "text-caution",
};

const TONE_CHIP: Record<Tone, string> = {
  neutral: "border-line text-mist",
  signal: "border-signal/60 text-signal bg-signal/10",
  breach: "border-breach/60 text-breach bg-breach/10",
  secure: "border-secure/60 text-secure bg-secure/10",
  caution: "border-caution/60 text-caution bg-caution/10",
};

export function StatusChip({
  tone = "neutral",
  children,
  live = false,
}: {
  tone?: Tone;
  children: ReactNode;
  live?: boolean;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-2 border px-2.5 py-1 reading text-[11px] font-medium tracking-[0.12em]",
        TONE_CHIP[tone],
      )}
    >
      {live && (
        <span className={clsx("h-1.5 w-1.5 rounded-full animate-pulse-dot", DOT[tone])} />
      )}
      {children}
    </span>
  );
}

const DOT: Record<Tone, string> = {
  neutral: "bg-mist",
  signal: "bg-signal",
  breach: "bg-breach",
  secure: "bg-secure",
  caution: "bg-caution",
};

export function Panel({
  title,
  aside,
  children,
  className,
  tone = "neutral",
}: {
  title?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  tone?: Tone;
}) {
  const edge =
    tone === "breach"
      ? "border-breach/50"
      : tone === "secure"
        ? "border-secure/50"
        : tone === "signal"
          ? "border-signal/40"
          : "border-line";
  return (
    <section className={clsx("panel", edge, className)}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-line-soft px-4 py-2.5">
          <h2 className="label">{title}</h2>
          {aside}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Field({
  label,
  value,
  tone = "neutral",
  mono = false,
}: {
  label: string;
  value: ReactNode;
  tone?: Tone;
  mono?: boolean;
}) {
  return (
    <div className="space-y-1">
      <p className="label">{label}</p>
      <p
        className={clsx(
          "text-[15px] leading-snug",
          mono && "reading",
          tone === "neutral" ? "text-chalk" : TONE_TEXT[tone],
        )}
      >
        {value}
      </p>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-dust">{children}</p>;
}
