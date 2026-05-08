"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import {
  DebugGenerationAsset,
  GenerationStatus,
  ProjectAvatar,
  Slide,
  VideoSettings,
  api,
} from "@/lib/api";

type SaveState = "idle" | "saving" | "saved" | "error";

function preview(value: string | null | undefined) {
  if (!value) return "Sin contenido";
  return value.length > 120 ? `${value.slice(0, 120)}...` : value;
}

function generationErrorMessage(status: GenerationStatus): string | null {
  if (status.status !== "failed") return null;
  if (status.error_code === "MISSING_RENDERED_PREVIEW") {
    return "Some slides are missing preview images. Please reprocess the presentation or upload it again.";
  }
  if (status.error_code === "GENERATION_JOB_STALLED") {
    return "The previous generation got stuck. Please try again.";
  }
  return status.error_message || status.error_code;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function slidePreviewUrl(slide: Slide): string | null {
  return slide.preview_image_url || null;
}

function initialDialogue(slide: Slide): string {
  return slide.dialogue || slide.notes || "";
}

export default function ProjectEditorPage() {
  const params = useParams<{ projectId: string }>();
  const searchParams = useSearchParams();
  const presentationId = searchParams.get("presentationId");

  const [slides, setSlides] = useState<Slide[]>([]);
  const [selectedSlideId, setSelectedSlideId] = useState<string | null>(null);
  const [dialogueBySlideId, setDialogueBySlideId] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(Boolean(presentationId));
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [isDirty, setIsDirty] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [videoSettings, setVideoSettings] = useState<VideoSettings | null>(null);
  const [elevenlabsApiKey, setElevenlabsApiKey] = useState("");
  const [elevenlabsVoiceId, setElevenlabsVoiceId] = useState("");
  const [wavespeedApiKey, setWavespeedApiKey] = useState("");
  const [projectAvatar, setProjectAvatar] = useState<ProjectAvatar | null>(null);
  const [avatarPreviewUrl, setAvatarPreviewUrl] = useState<string | null>(null);
  const [avatarUploadState, setAvatarUploadState] = useState<
    "idle" | "uploading" | "saved" | "error"
  >("idle");
  const [avatarUploadError, setAvatarUploadError] = useState<string | null>(null);

  const [videoMessage, setVideoMessage] = useState<string | null>(null);
  const [videoError, setVideoError] = useState<string | null>(null);
  const [isSavingVideoSettings, setIsSavingVideoSettings] = useState(false);
  const [isValidatingVideoSettings, setIsValidatingVideoSettings] = useState(false);
  const [isStartingGeneration, setIsStartingGeneration] = useState(false);
  const [generationStatus, setGenerationStatus] = useState<GenerationStatus>({
    status: "idle",
    progress: 0,
    current_slide: null,
    total_slides: null,
    message: null,
    error_code: null,
    error_message: null,
    final_video_url: null,
  });
  const [debugAssets, setDebugAssets] = useState<DebugGenerationAsset[]>([]);

  const selectedSlide = useMemo(
    () => slides.find((slide) => slide.id === selectedSlideId) ?? null,
    [selectedSlideId, slides],
  );
  const selectedDialogue = selectedSlide ? dialogueBySlideId[selectedSlide.id] || "" : "";
  const allSlidesHaveDialogue = useMemo(
    () =>
      slides.length > 0 &&
      slides.every((slide) => Boolean((dialogueBySlideId[slide.id] || "").trim())),
    [dialogueBySlideId, slides],
  );
  const allSlidesHavePreview = useMemo(
    () => slides.length > 0 && slides.every((slide) => Boolean(slidePreviewUrl(slide))),
    [slides],
  );
  const activeGenerationStatuses = useMemo<GenerationStatus["status"][]>(
    () => [
      "queued",
      "validating",
      "generating_audio",
      "generating_avatar",
      "rendering_slides",
      "composing_slide",
      "composing_video",
    ],
    [],
  );
  const isGenerationRunning = activeGenerationStatuses.includes(generationStatus.status);
  const generationButtonLabel =
    generationStatus.status === "failed"
      ? "Try Again"
      : generationStatus.final_video_url || generationStatus.status === "completed"
        ? "Regenerate Video"
        : "Generate Video";
  const hasAvatar =
    Boolean(projectAvatar) ||
    Boolean(videoSettings?.avatar_source_asset_id) ||
    Boolean(videoSettings?.using_debug_avatar_source);
  const canGenerateVideo = Boolean(
    videoSettings?.elevenlabs_api_key_masked &&
      videoSettings.elevenlabs_voice_id &&
      videoSettings.wavespeed_api_key_masked &&
      videoSettings.elevenlabs_valid &&
      videoSettings.wavespeed_valid &&
      hasAvatar &&
      allSlidesHaveDialogue &&
      allSlidesHavePreview &&
      !isDirty &&
      saveState !== "saving" &&
      !isGenerationRunning,
  );
  const generationFailureMessage = generationErrorMessage(generationStatus);

  useEffect(() => {
    async function loadSlides() {
      if (!presentationId) return;

      try {
        setError(null);
        const data = await api.presentations.listSlides(presentationId);
        setSlides(data);
        setSelectedSlideId(data[0]?.id ?? null);
        setDialogueBySlideId(
          Object.fromEntries(data.map((slide) => [slide.id, initialDialogue(slide)])),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudieron cargar los slides.");
      } finally {
        setIsLoading(false);
      }
    }

    void loadSlides();
  }, [presentationId]);

  useEffect(() => {
    async function loadVideoExportState() {
      try {
        const [settings, status, avatar] = await Promise.all([
          api.videoSettings.get(params.projectId),
          api.generation.status(params.projectId),
          api.projects.getAvatar(params.projectId),
        ]);
        setVideoSettings(settings);
        setElevenlabsVoiceId(settings.elevenlabs_voice_id || "");
        setGenerationStatus(status);
        if (avatar) {
          setProjectAvatar(avatar);
          setAvatarPreviewUrl(avatar.avatar_preview_url);
          setAvatarUploadState("saved");
        }
      } catch (err) {
        setVideoError(
          err instanceof Error ? err.message : "No se pudo cargar la configuracion de video.",
        );
      }
    }

    void loadVideoExportState();
  }, [params.projectId]);

  useEffect(() => {
    if (process.env.NODE_ENV === "production") return;
    if (!["completed", "failed"].includes(generationStatus.status)) return;

    async function loadDebugAssets() {
      try {
        const result = await api.generation.debugAssets(params.projectId);
        setDebugAssets(result.assets);
      } catch {
        setDebugAssets([]);
      }
    }

    void loadDebugAssets();
  }, [generationStatus.status, params.projectId]);

  useEffect(() => {
    if (!activeGenerationStatuses.includes(generationStatus.status)) return;

    const timer = window.setInterval(async () => {
      try {
        setGenerationStatus(await api.generation.status(params.projectId));
      } catch (err) {
        setVideoError(err instanceof Error ? err.message : "No se pudo actualizar el progreso.");
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [activeGenerationStatuses, generationStatus.status, params.projectId]);

  useEffect(() => {
    return () => {
      if (avatarPreviewUrl?.startsWith("blob:")) URL.revokeObjectURL(avatarPreviewUrl);
    };
  }, [avatarPreviewUrl]);

  function selectSlide(slide: Slide) {
    setSelectedSlideId(slide.id);
    setSaveState("idle");
    setSaveError(null);
  }

  function updateSelectedDialogue(value: string) {
    if (!selectedSlide) return;
    setDialogueBySlideId((current) => ({ ...current, [selectedSlide.id]: value }));
    setSaveState("idle");
    setSaveError(null);
    setIsDirty(true);
  }

  async function saveSelectedDialogue(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!selectedSlide) return;

    setSaveState("saving");
    setSaveError(null);
    try {
      const updated = await api.slides.update(selectedSlide.id, {
        notes: selectedDialogue,
        dialogue: selectedDialogue,
        metadata: {
          ...selectedSlide.metadata,
          dialogue: selectedDialogue,
        },
      });
      setSlides((current) =>
        current.map((slide) => (slide.id === updated.id ? updated : slide)),
      );
      setDialogueBySlideId((current) => ({
        ...current,
        [updated.id]: initialDialogue(updated),
      }));
      setSaveState("saved");
      setIsDirty(false);
    } catch (err) {
      setSaveState("error");
      setSaveError(err instanceof Error ? err.message : "No se pudo guardar el dialogo.");
    }
  }

  async function handleAvatarFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    if (avatarPreviewUrl?.startsWith("blob:")) URL.revokeObjectURL(avatarPreviewUrl);
    const localPreviewUrl = URL.createObjectURL(file);
    setAvatarPreviewUrl(localPreviewUrl);
    setAvatarUploadState("uploading");
    setAvatarUploadError(null);
    setVideoError(null);
    try {
      const uploaded = await api.projects.uploadAvatar(params.projectId, file);
      setProjectAvatar(uploaded);
      setAvatarUploadState("saved");
      setVideoSettings((current) =>
        current
          ? {
              ...current,
              avatar_source_asset_id: uploaded.avatar_asset_id,
              avatar_source_url: null,
            }
          : current,
      );
      setAvatarPreviewUrl(uploaded.avatar_preview_url);
      URL.revokeObjectURL(localPreviewUrl);
    } catch (err) {
      setAvatarUploadState("error");
      setAvatarUploadError(err instanceof Error ? err.message : "Avatar upload failed.");
    }
  }

  async function saveVideoSettings() {
    setIsSavingVideoSettings(true);
    setVideoError(null);
    setVideoMessage(null);
    try {
      const updated = await api.videoSettings.update(params.projectId, {
        elevenlabs_api_key: elevenlabsApiKey || null,
        elevenlabs_voice_id: elevenlabsVoiceId,
        wavespeed_api_key: wavespeedApiKey || null,
      });
      setVideoSettings(updated);
      setElevenlabsVoiceId(updated.elevenlabs_voice_id || "");
      setElevenlabsApiKey("");
      setWavespeedApiKey("");
      setVideoMessage("Settings saved.");
    } catch (err) {
      setVideoError(err instanceof Error ? err.message : "No se pudieron guardar los settings.");
    } finally {
      setIsSavingVideoSettings(false);
    }
  }

  async function validateVideoSettings() {
    setIsValidatingVideoSettings(true);
    setVideoError(null);
    setVideoMessage(null);
    try {
      const result = await api.videoSettings.validate(params.projectId);
      setVideoSettings(result);
      setVideoMessage(result.message);
    } catch (err) {
      setVideoError(err instanceof Error ? err.message : "No se pudieron validar las credenciales.");
    } finally {
      setIsValidatingVideoSettings(false);
    }
  }

  async function startVideoGeneration() {
    setIsStartingGeneration(true);
    setVideoError(null);
    setVideoMessage(null);
    try {
      await api.generation.start(params.projectId);
      setGenerationStatus(await api.generation.status(params.projectId));
      setVideoMessage("Generation queued.");
    } catch (err) {
      const message =
        err instanceof Error && err.message.includes("Slides missing rendered preview image")
          ? "Some slides are missing preview images. Please reprocess the presentation or upload it again."
          : err instanceof Error
            ? err.message
            : "No se pudo iniciar la generacion.";
      setVideoError(message);
    } finally {
      setIsStartingGeneration(false);
    }
  }

  return (
    <AppShell title="Video generation">
      <div className="mb-6 flex flex-col gap-2">
        <Link
          href={`/projects/${params.projectId}`}
          className="text-sm font-medium text-brand-700 hover:text-brand-800"
        >
          Volver al proyecto
        </Link>
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Slide Preview</h2>
          <p className="mt-1 text-sm text-gray-500">
            Revisa los slides renderizados, configura narracion, avatar y credenciales, y genera el video.
          </p>
        </div>
      </div>

      {!presentationId ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Falta el parametro presentationId.
        </div>
      ) : isLoading ? (
        <div className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-500 shadow-sm">
          Cargando slides...
        </div>
      ) : error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : slides.length === 0 ? (
        <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <p className="text-sm font-medium text-gray-900">Aun no hay slides disponibles.</p>
          <p className="mt-1 text-sm text-gray-500">
            Espera a que termine el parseo y vuelve a intentarlo.
          </p>
        </div>
      ) : (
        <div className="grid gap-5 xl:grid-cols-[280px_minmax(0,1fr)_360px]">
          <aside className="rounded-lg border border-gray-200 bg-white shadow-sm">
            <div className="border-b border-gray-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-gray-900">Slides</h3>
            </div>
            <div className="max-h-[760px] overflow-auto">
              {slides.map((slide) => {
                const isSelected = slide.id === selectedSlideId;
                const imageUrl = slidePreviewUrl(slide);
                return (
                  <button
                    key={slide.id}
                    type="button"
                    onClick={() => selectSlide(slide)}
                    className={`block w-full border-b border-gray-100 px-4 py-3 text-left transition-colors ${
                      isSelected ? "bg-brand-50" : "hover:bg-gray-50"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-xs font-medium uppercase text-gray-500">
                        Slide {slide.position}
                      </span>
                      {imageUrl ? (
                        <span className="text-xs font-medium text-green-700">Preview</span>
                      ) : (
                        <span className="text-xs font-medium text-amber-700">No preview</span>
                      )}
                    </div>
                    {imageUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={imageUrl}
                        alt={`Slide ${slide.position} preview`}
                        className="mt-2 aspect-video w-full rounded-md border border-gray-200 object-contain"
                      />
                    ) : null}
                    <p className="mt-2 text-sm font-semibold text-gray-900">
                      {slide.title || "Sin titulo"}
                    </p>
                    <p className="mt-1 text-xs text-gray-500">
                      {preview(dialogueBySlideId[slide.id] || slide.visible_text)}
                    </p>
                  </button>
                );
              })}
            </div>
          </aside>

          <main className="min-w-0 space-y-5">
            {selectedSlide ? (
              <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium uppercase text-gray-500">
                      Slide {selectedSlide.position}
                    </p>
                    <h3 className="text-sm font-semibold text-gray-900">
                      {selectedSlide.title || "Sin titulo"}
                    </h3>
                  </div>
                  <span className="rounded-full bg-gray-100 px-2 py-1 text-xs font-medium text-gray-700">
                    Rendered image source
                  </span>
                </div>
                <div className="rounded-lg border border-gray-200 bg-gray-100 p-3">
                  {slidePreviewUrl(selectedSlide) ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={slidePreviewUrl(selectedSlide) || ""}
                      alt={`Slide ${selectedSlide.position} rendered preview`}
                      className="mx-auto aspect-video w-full max-h-[620px] rounded-md bg-white object-contain"
                    />
                  ) : (
                    <div className="flex aspect-video items-center justify-center rounded-md bg-white text-sm text-amber-700">
                      No rendered slide preview is available.
                    </div>
                  )}
                </div>
              </section>
            ) : null}

            {selectedSlide ? (
              <form
                onSubmit={saveSelectedDialogue}
                className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
              >
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-gray-900">Narration</h3>
                    <p className="mt-1 text-xs text-gray-500">
                      This text is used for ElevenLabs audio. It does not change the slide image.
                    </p>
                  </div>
                  <button
                    type="submit"
                    disabled={saveState === "saving" || !selectedSlide}
                    className="rounded-md bg-brand-600 px-3 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-400"
                  >
                    {saveState === "saving" ? "Saving..." : "Save Narration"}
                  </button>
                </div>
                <textarea
                  value={selectedDialogue}
                  onChange={(event) => updateSelectedDialogue(event.target.value)}
                  placeholder="Escribe el dialogo para narracion"
                  className="min-h-36 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                />
                <div className="mt-2 min-h-5">
                  {saveState === "saved" ? (
                    <p className="text-xs font-medium text-green-700">Narration saved.</p>
                  ) : null}
                  {saveError ? <p className="text-xs font-medium text-red-700">{saveError}</p> : null}
                </div>
              </form>
            ) : null}
          </main>

          <aside className="space-y-5">
            <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <h3 className="text-sm font-semibold text-gray-900">Avatar</h3>
              <p className="mt-1 text-xs text-gray-500">
                Upload one presenter image. Placement uses the project default during composition.
              </p>
              <label className="mt-3 block">
                <span className="text-sm font-medium text-gray-700">Avatar image</span>
                <input
                  type="file"
                  accept="image/*"
                  onChange={handleAvatarFile}
                  disabled={avatarUploadState === "uploading"}
                  className="mt-1 w-full text-sm text-gray-700 file:mr-3 file:rounded-md file:border-0 file:bg-gray-100 file:px-3 file:py-2 file:text-sm file:font-semibold file:text-gray-700"
                />
              </label>
              {avatarPreviewUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={avatarPreviewUrl}
                  alt="Saved avatar"
                  className="mt-3 aspect-square w-32 rounded-md border border-gray-200 object-cover"
                />
              ) : null}
              {avatarUploadState === "uploading" ? (
                <p className="mt-2 text-xs text-blue-700">Uploading avatar...</p>
              ) : avatarUploadState === "saved" ? (
                <p className="mt-2 text-xs text-green-700">Avatar saved.</p>
              ) : avatarUploadState === "error" ? (
                <p className="mt-2 text-xs text-red-700">
                  {avatarUploadError || "Avatar upload failed."}
                </p>
              ) : (
                <p className="mt-2 text-xs text-gray-500">No avatar uploaded yet.</p>
              )}
            </section>

            <section className="space-y-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">
                    Video Generation Settings
                  </h3>
                  <p className="mt-1 text-xs text-gray-500">
                    Credentials are saved backend-side and are never returned raw.
                  </p>
                </div>
                <span
                  className={`rounded-full px-2 py-1 text-xs font-semibold ${
                    videoSettings?.validation_status === "valid"
                      ? "bg-green-100 text-green-700"
                      : videoSettings?.validation_status === "invalid"
                        ? "bg-red-100 text-red-700"
                        : videoSettings?.validation_status === "saved"
                          ? "bg-blue-100 text-blue-700"
                          : "bg-gray-200 text-gray-700"
                  }`}
                >
                  {videoSettings?.validation_status === "valid"
                    ? "Valid"
                    : videoSettings?.validation_status === "invalid"
                      ? "Invalid"
                      : videoSettings?.validation_status === "saved"
                        ? "Saved"
                        : "Not configured"}
                </span>
              </div>

              <label className="block">
                <span className="text-xs font-medium text-gray-600">ElevenLabs API Key</span>
                <input
                  type="password"
                  value={elevenlabsApiKey}
                  onChange={(event) => setElevenlabsApiKey(event.target.value)}
                  placeholder={videoSettings?.elevenlabs_api_key_masked || "Paste API key"}
                  className="mt-1 w-full rounded-md border border-gray-300 px-2 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                />
                {videoSettings?.elevenlabs_api_key_masked ? (
                  <span className="mt-1 block text-xs text-gray-500">
                    Saved: {videoSettings.elevenlabs_api_key_masked}
                  </span>
                ) : null}
              </label>

              <label className="block">
                <span className="text-xs font-medium text-gray-600">ElevenLabs Voice ID</span>
                <input
                  type="text"
                  value={elevenlabsVoiceId}
                  onChange={(event) => setElevenlabsVoiceId(event.target.value)}
                  className="mt-1 w-full rounded-md border border-gray-300 px-2 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                />
              </label>

              <label className="block">
                <span className="text-xs font-medium text-gray-600">WaveSpeed API Key</span>
                <input
                  type="password"
                  value={wavespeedApiKey}
                  onChange={(event) => setWavespeedApiKey(event.target.value)}
                  placeholder={videoSettings?.wavespeed_api_key_masked || "Paste API key"}
                  className="mt-1 w-full rounded-md border border-gray-300 px-2 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                />
                {videoSettings?.wavespeed_api_key_masked ? (
                  <span className="mt-1 block text-xs text-gray-500">
                    Saved: {videoSettings.wavespeed_api_key_masked}
                  </span>
                ) : null}
              </label>

              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={saveVideoSettings}
                  disabled={isSavingVideoSettings}
                  className="rounded-md bg-gray-900 px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-400"
                >
                  {isSavingVideoSettings ? "Saving..." : "Save Settings"}
                </button>
                <button
                  type="button"
                  onClick={validateVideoSettings}
                  disabled={isValidatingVideoSettings}
                  className="rounded-md border border-gray-300 px-3 py-2 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-400"
                >
                  {isValidatingVideoSettings ? "Testing..." : "Test Credentials"}
                </button>
              </div>
            </section>

            <section className="space-y-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <h3 className="text-sm font-semibold text-gray-900">Generation Progress</h3>

              {!allSlidesHavePreview ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Every slide needs a rendered image preview before video generation.
                </div>
              ) : null}
              {!allSlidesHaveDialogue ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Every slide needs narration before video generation.
                </div>
              ) : null}
              {isDirty ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Save narration before generating video.
                </div>
              ) : null}
              {!hasAvatar ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Upload an avatar image before generating video.
                </div>
              ) : null}
              {videoMessage ? (
                <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-700">
                  {videoMessage}
                </div>
              ) : null}
              {videoError ? (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                  {videoError}
                </div>
              ) : null}

              <div className="space-y-2 rounded-md border border-gray-200 bg-gray-50 p-3">
                <div className="flex items-center justify-between text-xs text-gray-600">
                  <span>{generationStatus.message || "Idle"}</span>
                  <span>{Math.round(generationStatus.progress)}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-gray-200">
                  <div
                    className="h-full rounded-full bg-brand-600 transition-all"
                    style={{ width: `${clamp(generationStatus.progress, 0, 100)}%` }}
                  />
                </div>
                {generationStatus.current_slide && generationStatus.total_slides ? (
                  <p className="text-xs text-gray-500">
                    Slide {generationStatus.current_slide} of {generationStatus.total_slides}
                  </p>
                ) : null}
                {generationFailureMessage ? (
                  <p className="text-xs text-red-700">{generationFailureMessage}</p>
                ) : null}
              </div>

              <button
                type="button"
                onClick={startVideoGeneration}
                disabled={!canGenerateVideo || isStartingGeneration}
                className="w-full rounded-md bg-brand-600 px-3 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-400"
              >
                {isStartingGeneration
                  ? "Starting..."
                  : isGenerationRunning
                    ? generationStatus.message || "Generating..."
                    : generationButtonLabel}
              </button>

              {generationStatus.final_video_url ? (
                <div className="space-y-2">
                  <video
                    controls
                    src={generationStatus.final_video_url}
                    className="aspect-video w-full rounded-md border border-gray-200 bg-black"
                  />
                  <a
                    href={generationStatus.final_video_url}
                    className="block rounded-md border border-gray-300 px-3 py-2 text-center text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50"
                  >
                    Download MP4
                  </a>
                </div>
              ) : null}

              {process.env.NODE_ENV !== "production" && debugAssets.length > 0 ? (
                <div className="space-y-2 rounded-md border border-dashed border-gray-300 bg-white p-3">
                  <h5 className="text-xs font-semibold uppercase text-gray-500">Debug assets</h5>
                  <div className="space-y-1">
                    {debugAssets.map((asset) => (
                      <a
                        key={asset.id}
                        href={asset.download_url}
                        className="block truncate text-xs font-medium text-brand-700 hover:text-brand-800"
                      >
                        {asset.asset_type}
                        {asset.slide_id ? ` / ${asset.filename}` : ""}
                      </a>
                    ))}
                  </div>
                </div>
              ) : null}
            </section>
          </aside>
        </div>
      )}
    </AppShell>
  );
}
