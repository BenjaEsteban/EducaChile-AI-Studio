"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { ProjectGenerationConfig, ProviderCredentialStatus, api } from "@/lib/api";

type SaveState = "idle" | "saving" | "saved" | "error";

interface FormState {
  tts_provider: "gemini" | "elevenlabs";
  video_provider: "wavespeed";
  voice_id: string;
  voice_name: string;
  avatar_id: string;
  gemini_api_key: string;
  elevenlabs_api_key: string;
  wavespeed_api_key: string;
  resolution: string;
  aspect_ratio: string;
  language: string;
  subtitles_enabled: boolean;
  background_music_enabled: boolean;
}

const defaultForm: FormState = {
  tts_provider: "gemini",
  video_provider: "wavespeed",
  voice_id: "",
  voice_name: "",
  avatar_id: "",
  gemini_api_key: "",
  elevenlabs_api_key: "",
  wavespeed_api_key: "",
  resolution: "1080p",
  aspect_ratio: "16:9",
  language: "es",
  subtitles_enabled: false,
  background_music_enabled: false,
};

function formFromConfig(config: ProjectGenerationConfig): FormState {
  return {
    tts_provider: config.tts_provider,
    video_provider: config.video_provider,
    voice_id: config.voice_id || "",
    voice_name: config.voice_name || "",
    avatar_id: config.avatar_id || "",
    gemini_api_key: "",
    elevenlabs_api_key: "",
    wavespeed_api_key: "",
    resolution: config.resolution,
    aspect_ratio: config.aspect_ratio,
    language: config.language,
    subtitles_enabled: config.subtitles_enabled,
    background_music_enabled: config.background_music_enabled,
  };
}

export default function ProjectSettingsPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;
  const [config, setConfig] = useState<ProjectGenerationConfig | null>(null);
  const [form, setForm] = useState<FormState>(defaultForm);
  const [isLoading, setIsLoading] = useState(true);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [credentials, setCredentials] = useState<ProviderCredentialStatus[]>([]);
  const [credentialState, setCredentialState] = useState<string | null>(null);

  useEffect(() => {
    async function loadConfig() {
      try {
        setError(null);
        const [data, statuses] = await Promise.all([
          api.projects.getGenerationConfig(projectId),
          api.providerCredentials.listStatus(),
        ]);
        setConfig(data);
        setCredentials(statuses);
        setForm(formFromConfig(data));
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudo cargar la configuracion.");
      } finally {
        setIsLoading(false);
      }
    }

    void loadConfig();
  }, [projectId]);

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setSaveState("idle");
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaveState("saving");
    setError(null);
    try {
      const payload = {
        tts_provider: form.tts_provider,
        video_provider: form.video_provider,
        voice_id: form.voice_id.trim() || null,
        voice_name: form.voice_name.trim() || null,
        avatar_id: form.avatar_id.trim() || null,
        resolution: form.resolution,
        aspect_ratio: form.aspect_ratio,
        ai_provider: "gemini" as const,
        language: form.language,
        output_format: "mp4" as const,
        subtitles_enabled: form.subtitles_enabled,
        background_music_enabled: form.background_music_enabled,
      };
      const saved = await api.projects.updateGenerationConfig(projectId, payload);
      setConfig(saved);
      setForm(formFromConfig(saved));
      setSaveState("saved");
    } catch (err) {
      setSaveState("error");
      setError(err instanceof Error ? err.message : "No se pudo guardar la configuracion.");
    }
  }

  async function saveCredential(
    provider_name: ProviderCredentialStatus["provider_name"],
    provider_type: ProviderCredentialStatus["provider_type"],
    api_key: string,
  ) {
    if (!api_key.trim()) return;
    setCredentialState(`Guardando ${provider_name}...`);
    await api.providerCredentials.save({
      provider_name,
      provider_type,
      api_key: api_key.trim(),
    });
    const statuses = await api.providerCredentials.listStatus();
    setCredentials(statuses);
    setCredentialState(`${provider_name} guardado. Prueba la conexion.`);
  }

  async function validateCredential(
    provider_name: ProviderCredentialStatus["provider_name"],
    provider_type: ProviderCredentialStatus["provider_type"],
  ) {
    setCredentialState(`Probando ${provider_name}...`);
    const result = await api.providerCredentials.validate(provider_name, provider_type);
    const statuses = await api.providerCredentials.listStatus();
    setCredentials(statuses);
    setCredentialState(result.message);
  }

  function credentialFor(
    provider_name: ProviderCredentialStatus["provider_name"],
    provider_type: ProviderCredentialStatus["provider_type"],
  ) {
    return credentials.find(
      (credential) =>
        credential.provider_name === provider_name && credential.provider_type === provider_type,
    );
  }

  return (
    <AppShell title="Configuracion">
      <div className="mb-6 flex flex-col gap-2">
        <Link
          href={`/projects/${projectId}`}
          className="text-sm font-medium text-brand-700 hover:text-brand-800"
        >
          Volver al proyecto
        </Link>
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Configuracion de generacion</h2>
          <p className="mt-1 text-sm text-gray-500">
            Define proveedores, voz, formato y claves para los proximos pasos de generacion.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-500 shadow-sm">
          Cargando configuracion...
        </div>
      ) : (
        <div className="space-y-6">
        <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <h3 className="text-sm font-semibold text-gray-900">Provider API keys</h3>
          <p className="mt-1 text-sm text-gray-500">
            Las claves se envian una vez al backend, se guardan cifradas y despues solo se muestran enmascaradas.
          </p>
          {credentialState ? (
            <div className="mt-4 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
              {credentialState}
            </div>
          ) : null}
          <div className="mt-5 grid gap-4 md:grid-cols-3">
            <ProviderKeyCard
              label="Gemini AI"
              providerName="gemini"
              providerType="ai"
              value={form.gemini_api_key}
              status={credentialFor("gemini", "ai")}
              onChange={(value) => updateField("gemini_api_key", value)}
              onSave={() => saveCredential("gemini", "ai", form.gemini_api_key)}
              onValidate={() => validateCredential("gemini", "ai")}
            />
            <ProviderKeyCard
              label={form.tts_provider === "elevenlabs" ? "ElevenLabs TTS" : "Gemini TTS"}
              providerName={form.tts_provider}
              providerType="tts"
              value={
                form.tts_provider === "elevenlabs"
                  ? form.elevenlabs_api_key
                  : form.gemini_api_key
              }
              status={credentialFor(form.tts_provider, "tts")}
              onChange={(value) =>
                form.tts_provider === "elevenlabs"
                  ? updateField("elevenlabs_api_key", value)
                  : updateField("gemini_api_key", value)
              }
              onSave={() =>
                saveCredential(
                  form.tts_provider,
                  "tts",
                  form.tts_provider === "elevenlabs"
                    ? form.elevenlabs_api_key
                    : form.gemini_api_key,
                )
              }
              onValidate={() => validateCredential(form.tts_provider, "tts")}
            />
            <ProviderKeyCard
              label="Wavespeed Avatar"
              providerName="wavespeed"
              providerType="avatar_video"
              value={form.wavespeed_api_key}
              status={credentialFor("wavespeed", "avatar_video")}
              onChange={(value) => updateField("wavespeed_api_key", value)}
              onSave={() => saveCredential("wavespeed", "avatar_video", form.wavespeed_api_key)}
              onValidate={() => validateCredential("wavespeed", "avatar_video")}
            />
          </div>
        </section>

        <form onSubmit={handleSubmit} className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          {error ? (
            <div className="mb-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          ) : null}

          <div className="grid gap-5 md:grid-cols-2">
            <label className="block">
              <span className="text-sm font-medium text-gray-700">TTS provider</span>
              <select
                value={form.tts_provider}
                onChange={(event) =>
                  updateField("tts_provider", event.target.value as FormState["tts_provider"])
                }
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              >
                <option value="gemini">Gemini</option>
                <option value="elevenlabs">ElevenLabs</option>
              </select>
            </label>

            <label className="block">
              <span className="text-sm font-medium text-gray-700">Video provider</span>
              <select
                value={form.video_provider}
                onChange={(event) =>
                  updateField("video_provider", event.target.value as FormState["video_provider"])
                }
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              >
                <option value="wavespeed">Wavespeed</option>
              </select>
            </label>

            <TextInput label="Voice ID" value={form.voice_id} onChange={(value) => updateField("voice_id", value)} />
            <TextInput label="Voice name" value={form.voice_name} onChange={(value) => updateField("voice_name", value)} />
            <TextInput label="Avatar ID" value={form.avatar_id} onChange={(value) => updateField("avatar_id", value)} />
            <TextInput label="Resolution" value={form.resolution} onChange={(value) => updateField("resolution", value)} />
            <TextInput label="Aspect ratio" value={form.aspect_ratio} onChange={(value) => updateField("aspect_ratio", value)} />
            <TextInput label="Language" value={form.language} onChange={(value) => updateField("language", value)} />
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={form.subtitles_enabled}
                onChange={(event) => updateField("subtitles_enabled", event.target.checked)}
              />
              Subtitles enabled
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={form.background_music_enabled}
                onChange={(event) => updateField("background_music_enabled", event.target.checked)}
              />
              Background music enabled
            </label>
          </div>

          <div className="mt-6 flex items-center gap-3">
            <button
              type="submit"
              disabled={saveState === "saving"}
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-400"
            >
              {saveState === "saving" ? "Guardando..." : "Guardar configuracion"}
            </button>
            {saveState === "saved" ? (
              <span className="text-sm font-medium text-green-700">Guardado</span>
            ) : null}
            {saveState === "error" ? (
              <span className="text-sm font-medium text-red-700">Error</span>
            ) : null}
          </div>
        </form>
        </div>
      )}
    </AppShell>
  );
}

function TextInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-gray-700">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
      />
    </label>
  );
}

function ProviderKeyCard({
  label,
  providerName,
  providerType,
  status,
  value,
  onChange,
  onSave,
  onValidate,
}: {
  label: string;
  providerName: string;
  providerType: string;
  status?: ProviderCredentialStatus;
  value: string;
  onChange: (value: string) => void;
  onSave: () => void;
  onValidate: () => void;
}) {
  return (
    <div className="rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-gray-900">{label}</p>
          <p className="text-xs text-gray-500">{providerName} / {providerType}</p>
        </div>
        <span className="rounded-full bg-gray-100 px-2 py-1 text-xs font-medium text-gray-700">
          {status?.status || "not_configured"}
        </span>
      </div>
      {status?.masked_api_key ? (
        <p className="mt-3 text-xs text-gray-500">Actual: {status.masked_api_key}</p>
      ) : null}
      <input
        type="password"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Pegar API key"
        className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
      />
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={onSave}
          className="rounded-md bg-brand-600 px-3 py-2 text-xs font-semibold text-white"
        >
          Save API Key
        </button>
        <button
          type="button"
          onClick={onValidate}
          className="rounded-md border border-gray-300 px-3 py-2 text-xs font-semibold text-gray-700"
        >
          Test Connection
        </button>
      </div>
    </div>
  );
}
