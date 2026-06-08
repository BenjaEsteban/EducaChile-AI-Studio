"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, ProviderCredentialStatus, api } from "@/lib/api";

type SaveState = "idle" | "saving" | "saved" | "error";
type TestState = "idle" | "testing" | "done";

function statusLabel(status: ProviderCredentialStatus["status"] | undefined): {
  text: string;
  cls: string;
} {
  switch (status) {
    case "valid":
      return { text: "Válido", cls: "bg-green-100 text-green-700" };
    case "invalid":
    case "expired_or_revoked":
      return { text: "Inválido", cls: "bg-red-100 text-red-700" };
    case "configured":
      return { text: "Guardado", cls: "bg-blue-100 text-blue-700" };
    default:
      return { text: "Sin configurar", cls: "bg-gray-200 text-gray-600" };
  }
}

/**
 * Global "Configuración" gear menu for the dashboard navbar.
 *
 * Manages org-wide AI credentials (ElevenLabs + WaveSpeed) by reusing the
 * existing `/api/v1/provider-credentials` endpoints. Keys are stored backend
 * side (encrypted) and only ever returned masked (e.g. ************bfc6); raw
 * keys are never exposed to the frontend.
 */
export function GlobalSettingsMenu() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const [credentials, setCredentials] = useState<ProviderCredentialStatus[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Form inputs (empty = keep existing saved value)
  const [elevenKey, setElevenKey] = useState("");
  const [elevenVoiceId, setElevenVoiceId] = useState("");
  const [wavespeedKey, setWavespeedKey] = useState("");

  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [testState, setTestState] = useState<TestState>("idle");
  const [testMessage, setTestMessage] = useState<string | null>(null);

  const elevenCred = credentials.find(
    (c) => c.provider_name === "elevenlabs" && c.provider_type === "tts",
  );
  const wavespeedCred = credentials.find(
    (c) => c.provider_name === "wavespeed" && c.provider_type === "avatar_video",
  );

  const loadCredentials = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const list = await api.providerCredentials.listStatus();
      setCredentials(list);
      const eleven = list.find(
        (c) => c.provider_name === "elevenlabs" && c.provider_type === "tts",
      );
      // Voice id is not secret — prefill it so the user can see/edit it.
      setElevenVoiceId(eleven?.voice_id ?? "");
    } catch (err) {
      setLoadError(
        err instanceof ApiError ? err.message : "No se pudieron cargar las credenciales.",
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Load when opened
  useEffect(() => {
    if (open) void loadCredentials();
  }, [open, loadCredentials]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  async function handleSave() {
    setSaveState("saving");
    setSaveError(null);
    setTestState("idle");
    setTestMessage(null);
    try {
      // ElevenLabs: save key (if entered) and/or voice id.
      const trimmedElevenKey = elevenKey.trim();
      const trimmedVoiceId = elevenVoiceId.trim();
      if (trimmedElevenKey || trimmedVoiceId || elevenCred) {
        if (trimmedElevenKey || elevenCred) {
          await api.providerCredentials.save({
            provider_name: "elevenlabs",
            provider_type: "tts",
            api_key: trimmedElevenKey || undefined,
            voice_id: trimmedVoiceId,
          });
        }
      }
      // WaveSpeed: save key (only when a new one is entered).
      const trimmedWavespeed = wavespeedKey.trim();
      if (trimmedWavespeed) {
        await api.providerCredentials.save({
          provider_name: "wavespeed",
          provider_type: "avatar_video",
          api_key: trimmedWavespeed,
        });
      }
      setElevenKey("");
      setWavespeedKey("");
      await loadCredentials();
      setSaveState("saved");
    } catch (err) {
      setSaveState("error");
      setSaveError(
        err instanceof ApiError ? err.message : "No se pudo guardar la configuración.",
      );
    }
  }

  async function handleTest() {
    setTestState("testing");
    setTestMessage(null);
    try {
      const results: string[] = [];
      if (elevenCred?.id) {
        const r = await api.providerCredentials.validate("elevenlabs", "tts");
        results.push(`ElevenLabs: ${r.valid ? "Válido" : "Inválido"}`);
      }
      if (wavespeedCred?.id) {
        const r = await api.providerCredentials.validate("wavespeed", "avatar_video");
        results.push(`WaveSpeed: ${r.valid ? "Válido" : "Inválido"}`);
      }
      await loadCredentials();
      setTestState("done");
      setTestMessage(
        results.length ? results.join(" · ") : "No hay credenciales guardadas para probar.",
      );
    } catch (err) {
      setTestState("done");
      setTestMessage(
        err instanceof ApiError ? err.message : "No se pudieron probar las credenciales.",
      );
    }
  }

  const inputCls =
    "mt-1 w-full rounded-md border border-gray-300 px-2 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100";

  const elevenStatus = statusLabel(elevenCred?.status);
  const wavespeedStatus = statusLabel(wavespeedCred?.status);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label="Configuración"
        title="Configuración"
        className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-slate-300 bg-white text-slate-700 transition hover:bg-slate-50"
      >
        {/* gear icon */}
        <svg
          className="h-5 w-5"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
        </svg>
      </button>

      {open ? (
        <div
          role="dialog"
          aria-label="Configuración"
          className="absolute right-0 z-50 mt-2 w-[340px] max-w-[calc(100vw-2rem)] rounded-lg border border-gray-200 bg-white p-4 shadow-xl"
        >
          <div className="mb-3">
            <h3 className="text-sm font-semibold text-gray-900">Configuración</h3>
            <p className="mt-0.5 text-xs text-gray-500">
              Credenciales de IA globales para todos los videos.
            </p>
          </div>

          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Credenciales de IA
            </span>
          </div>

          {isLoading ? (
            <p className="py-4 text-center text-xs text-gray-500">Cargando…</p>
          ) : (
            <div className="space-y-4">
              {/* Non-blocking load error: still show the form so the user can
                  enter new credentials. */}
              {loadError ? (
                <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Error al cargar credenciales. Puedes ingresar y guardar nuevas credenciales igualmente.
                </p>
              ) : null}
              {/* ElevenLabs API Key */}
              <label className="block">
                <span className="flex items-center justify-between text-xs font-medium text-gray-600">
                  ElevenLabs API Key
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${elevenStatus.cls}`}>
                    {elevenStatus.text}
                  </span>
                </span>
                <input
                  type="password"
                  value={elevenKey}
                  onChange={(e) => setElevenKey(e.target.value)}
                  placeholder={elevenCred?.masked_api_key || "Pega la API key"}
                  className={inputCls}
                />
                {elevenCred?.masked_api_key ? (
                  <span className="mt-1 block text-xs text-gray-500">
                    Guardado: {elevenCred.masked_api_key}
                  </span>
                ) : null}
              </label>

              {/* ElevenLabs Voice ID */}
              <label className="block">
                <span className="text-xs font-medium text-gray-600">ElevenLabs Voice ID</span>
                <input
                  type="text"
                  value={elevenVoiceId}
                  onChange={(e) => setElevenVoiceId(e.target.value)}
                  placeholder="Ej: lLsDvdl6OjtZfLJPM2HA"
                  className={inputCls}
                />
              </label>

              {/* WaveSpeed API Key */}
              <label className="block">
                <span className="flex items-center justify-between text-xs font-medium text-gray-600">
                  WaveSpeed API Key
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${wavespeedStatus.cls}`}>
                    {wavespeedStatus.text}
                  </span>
                </span>
                <input
                  type="password"
                  value={wavespeedKey}
                  onChange={(e) => setWavespeedKey(e.target.value)}
                  placeholder={wavespeedCred?.masked_api_key || "Pega la API key"}
                  className={inputCls}
                />
                {wavespeedCred?.masked_api_key ? (
                  <span className="mt-1 block text-xs text-gray-500">
                    Guardado: {wavespeedCred.masked_api_key}
                  </span>
                ) : null}
              </label>

              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={saveState === "saving"}
                  className="rounded-md bg-brand-600 px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-400"
                >
                  {saveState === "saving" ? "Guardando…" : "Guardar configuración"}
                </button>
                <button
                  type="button"
                  onClick={handleTest}
                  disabled={testState === "testing"}
                  className="rounded-md border border-gray-300 px-3 py-2 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-400"
                >
                  {testState === "testing" ? "Probando…" : "Probar credenciales"}
                </button>
              </div>

              <div className="min-h-4 space-y-1">
                {saveState === "saved" ? (
                  <p className="text-xs font-medium text-green-700">Guardado correctamente.</p>
                ) : null}
                {saveError ? (
                  <p className="text-xs font-medium text-red-700">{saveError}</p>
                ) : null}
                {testMessage ? (
                  <p className="text-xs font-medium text-gray-600">{testMessage}</p>
                ) : null}
              </div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
