import Link from "next/link";
import { api } from "@/lib/api";

async function getBackendStatus() {
  try {
    const data = await api.health();
    return data.status;
  } catch {
    return "unreachable";
  }
}

export default async function HomePage() {
  const backendStatus = await getBackendStatus();
  const isOnline = backendStatus === "ok";

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-br from-brand-50 to-white px-4">
      <div className="w-full max-w-2xl text-center">
        <span className="mb-4 inline-block rounded-full bg-brand-50 px-3 py-1 text-xs font-medium text-brand-600">
          AI-Powered Education
        </span>

        <h1 className="mb-4 text-4xl font-bold tracking-tight text-gray-900 sm:text-5xl">
          Educa Chile AI Video Platform
        </h1>

        <p className="mb-8 text-lg text-gray-500">
          Generate educational videos from PowerPoint presentations using AI
        </p>

        <div className="mb-10 flex items-center justify-center gap-2 text-sm">
          <span
            className={`h-2 w-2 rounded-full ${isOnline ? "bg-green-500" : "bg-red-400"}`}
          />
          <span className="text-gray-500">
            Backend:{" "}
            <span className={isOnline ? "text-green-600 font-medium" : "text-red-500 font-medium"}>
              {isOnline ? "connected" : backendStatus}
            </span>
          </span>
        </div>

        <Link
          href="/dashboard"
          className="rounded-lg bg-brand-600 px-6 py-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700"
        >
          Go to Dashboard →
        </Link>
      </div>
    </div>
  );
}
