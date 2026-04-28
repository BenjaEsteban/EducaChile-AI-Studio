"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Home" },
  { href: "/dashboard", label: "Dashboard" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex h-screen w-56 flex-col border-r border-gray-200 bg-white px-4 py-6">
      <span className="mb-8 text-lg font-bold text-brand-600">EducaChile</span>
      <nav className="flex flex-col gap-1">
        {links.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              pathname === href
                ? "bg-brand-50 text-brand-700"
                : "text-gray-600 hover:bg-gray-100"
            }`}
          >
            {label}
          </Link>
        ))}
      </nav>
    </aside>
  );
}
