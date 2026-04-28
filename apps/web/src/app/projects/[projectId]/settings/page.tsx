"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { ProjectGenerationConfig, api } from "@/lib/api";

type SaveState = "idle" | "saving" | "saved" | "error";

interface FormState {
  tts_provider: "gemini" | "elevenlabs";
  video_provider: "wavespeed";
  voice_id: string;
  voice_name: string;
  gemini_api_key: string;
  elevenlabs_api_key: string;
  wavespeed_api_key: string;
  resolution: string;
  aspect_ratio: string;
}

const defaultForm: FormState = {
  tts_provider: "gemini",
  video_provider: "wavespeed",
  voice_id: "",
  voice_name: "",
  gemini_api_key: "",
  elevenlabs_api_key: "",
  wavespeed_api_key: "",
  resolution: "1920x1080",
  aspect_ratio: "16:9",
};

function formFromConfig(config: ProjectGenerationConfig): FormState {
  return {
    tts_provider: config.tts_provider,
    video_provider: config.video_provider,
    voice_id: config.voice_id || "",
    voice_name: config.voice_name || "",
    gemini_api_key: "",
    elevenlabs_api_key: "",
    wavespeed_api_key: "",
    resolution: config.resolution,
    aspect_ratio: config.aspect_ratio,
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

  useEffect(() => {
    async function loadConfig() {
      try {
        setError(null);
        const data = await api.projects.getGenerationConfig(projectId);
        setConfig(data);
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
        resolution: form.resolution,
        aspect_ratio: form.aspect_ratio,
        ...(form.gemini_api_key.trim()
          ? { gemini_api_key: form.gemini_api_key.trim() }
          : {}),
        ...(form.elevenlabs_api_key.trim()
          ? { elevenlabs_api_key: form.elevenlabs_api_key.trim() }
          : {}),
        ...(form.wavespeed_api_key.trim()
          ? { wavespeed_api_key: form.wavespeed_api_key.trim() }
          : {}),
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
            <TextInput label="Resolution" value={form.resolution} onChange={(value) => updateField("resolution", value)} />
            <TextInput label="Aspect ratio" value={form.aspect_ratio} onChange={(value) => updateField("aspect_ratio", value)} />
            <SecretInput
              label="Gemini API Key"
              masked={config?.gemini_api_key}
              value={form.gemini_api_key}
              onChange={(value) => updateField("gemini_api_key", value)}
            />
            <SecretInput
              label="ElevenLabs API Key"
              masked={config?.elevenlabs_api_key}
              value={form.elevenlabs_api_key}
              onChange={(value) => updateField("elevenlabs_api_key", value)}
            />
            <SecretInput
              label="Wavespeed API Key"
              masked={config?.wavespeed_api_key}
              value={form.wavespeed_api_key}
              onChange={(value) => updateField("wavespeed_api_key", value)}
            />
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

function SecretInput({
  label,
  masked,
  value,
  onChange,
}: {
  label: string;
  masked?: string | null;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-gray-700">{label}</span>
      {masked ? <span className="ml-2 text-xs text-gray-500">Actual: {masked}</span> : null}
      <input
        type="password"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={masked ? "Dejar vacio para conservar" : "Pegar API key"}
        className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
      />
    </label>
  );
}
