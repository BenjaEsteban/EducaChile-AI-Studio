"use client";

import { ReactNode, useId, useState } from "react";

interface CollapsibleSectionProps {
  title: string;
  icon?: ReactNode;
  /** Optional element rendered on the right of the header (e.g. a status badge). */
  badge?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}

/**
 * Inline accordion card. Expands/collapses within normal document flow so the
 * content below is pushed down rather than overlaid. Smoothly animates height
 * using the grid-template-rows 0fr → 1fr technique.
 */
export function CollapsibleSection({
  title,
  icon,
  badge,
  defaultOpen = false,
  children,
}: CollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  const contentId = useId();

  return (
    <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={contentId}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left transition-colors hover:bg-gray-50"
      >
        <span className="flex min-w-0 items-center gap-2 text-sm font-semibold text-gray-900">
          {icon}
          <span className="truncate">{title}</span>
        </span>
        <span className="flex flex-shrink-0 items-center gap-2">
          {badge}
          <svg
            className={`h-4 w-4 text-gray-400 transition-transform duration-300 ${
              open ? "rotate-180" : ""
            }`}
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
        </span>
      </button>

      <div
        id={contentId}
        className={`grid transition-[grid-template-rows] duration-300 ease-in-out ${
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
        }`}
      >
        <div className="overflow-hidden">
          <div className="border-t border-gray-100">{children}</div>
        </div>
      </div>
    </section>
  );
}
