"use client";

import { ReactNode, useEffect, useRef, useState } from "react";

interface NavPopoverProps {
  label: string;
  icon?: ReactNode;
  /** Optional element rendered on the right of the trigger (e.g. a status dot). */
  badge?: ReactNode;
  /** Visual emphasis for primary actions. */
  variant?: "default" | "primary";
  /** Popover width in px (content area). */
  width?: number;
  children: ReactNode;
}

/**
 * A navbar dropdown/popover: a trigger button plus an inline-positioned panel
 * that closes on outside-click or Escape. Used to keep the editor chrome inside
 * a single navbar (no sidebar).
 */
export function NavPopover({
  label,
  icon,
  badge,
  variant = "default",
  width = 360,
  children,
}: NavPopoverProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const triggerCls =
    variant === "primary"
      ? "inline-flex items-center gap-1.5 rounded-md bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700"
      : "inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50";

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={triggerCls}
      >
        {icon}
        <span>{label}</span>
        {badge}
        <svg
          className={`h-4 w-4 opacity-70 transition-transform ${open ? "rotate-180" : ""}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open ? (
        <div
          role="dialog"
          aria-label={label}
          style={{ width }}
          className="absolute right-0 z-40 mt-2 max-h-[78vh] max-w-[calc(100vw-2rem)] overflow-auto rounded-lg border border-gray-200 bg-white shadow-xl"
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}
