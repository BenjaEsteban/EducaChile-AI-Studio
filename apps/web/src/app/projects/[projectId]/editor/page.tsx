"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  ChangeEvent,
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { AvatarSettingsPanel } from "@/components/editor/AvatarSettingsPanel";
import { CollapsibleSection } from "@/components/editor/CollapsibleSection";
import {
  CANVAS_H,
  CANVAS_W,
  DebugGenerationAsset,
  DEFAULT_SLIDE_AVATAR,
  GenerationStatus,
  ProjectAvatar,
  Slide,
  SlideAvatarMeta,
  VideoSettings,
  api,
  buildSlideAvatarPatch,
  extractSlideAvatarMeta,
} from "@/lib/api";
import { AppShell } from "@/components/layout/AppShell";

// ── helpers ──────────────────────────────────────────────────────────────────

type SaveState = "idle" | "saving" | "saved" | "error";

function preview(value: string | null | undefined) {
  if (!value) return "Sin contenido";
  return value.length > 120 ? `${value.slice(0, 120)}...` : value;
}

function generationErrorMessage(status: GenerationStatus): string | null {
  if (status.status !== "failed") return null;
  if (status.error_code === "MISSING_RENDERED_PREVIEW")
    return "Some slides are missing preview images. Please reprocess the presentation or upload it again.";
  if (status.error_code === "MISSING_AVATAR_ASSET")
    return "Please upload an avatar image before generating the video.";
  if (status.error_code === "MISSING_WAVESPEED_API_KEY")
    return "Please save a WaveSpeed API key before generating the video.";
  if (status.error_code === "INVALID_WAVESPEED_CREDENTIALS")
    return "WaveSpeed credentials were rejected. Please verify the API key.";
  if (status.error_code === "GENERATION_JOB_STALLED")
    return "The previous generation got stuck. Please try again.";
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

function isFreshHeartbeat(updatedAt: string | null | undefined) {
  if (!updatedAt) return false;
  const timestamp = new Date(updatedAt).getTime();
  if (Number.isNaN(timestamp)) return false;
  return Date.now() - timestamp < 2 * 60 * 1000;
}

// ── component ─────────────────────────────────────────────────────────────────

export default function ProjectEditorPage() {
  const params = useParams<{ projectId: string }>();
  const searchParams = useSearchParams();
  const presentationId = searchParams.get("presentationId");

  // ── slide data ──
  const [slides, setSlides] = useState<Slide[]>([]);
  const [selectedSlideId, setSelectedSlideId] = useState<string | null>(null);
  const [dialogueBySlideId, setDialogueBySlideId] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(Boolean(presentationId));
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [isDirty, setIsDirty] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // ── avatar meta (per-slide) ──
  const [slideAvatarMeta, setSlideAvatarMeta] = useState<Record<string, SlideAvatarMeta>>({});
  const [isSavingSlideAvatarMeta, setIsSavingSlideAvatarMeta] = useState(false);
  const [isApplyingToAll, setIsApplyingToAll] = useState(false);
  const [avatarMetaSaveState, setAvatarMetaSaveState] = useState<
    "idle" | "saved" | "error"
  >("idle");

  // ── project-level avatar ──
  const [videoSettings, setVideoSettings] = useState<VideoSettings | null>(null);
  const [projectAvatar, setProjectAvatar] = useState<ProjectAvatar | null>(null);
  const [avatarPreviewUrl, setAvatarPreviewUrl] = useState<string | null>(null);
  const [avatarUploadState, setAvatarUploadState] = useState<
    "idle" | "uploading" | "saved" | "error"
  >("idle");
  const [avatarUploadError, setAvatarUploadError] = useState<string | null>(null);

  // ── video generation ──
  const [videoMessage, setVideoMessage] = useState<string | null>(null);
  const [videoError, setVideoError] = useState<string | null>(null);
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
    updated_at: null,
  });
  const [debugAssets, setDebugAssets] = useState<DebugGenerationAsset[]>([]);

  // ── drag state for avatar overlay ──
  const canvasRef = useRef<HTMLDivElement>(null);
  const isDraggingRef = useRef(false);
  const isResizingRef = useRef(false);
  const [isDragging, setIsDragging] = useState(false);

  // ── derived ──
  const selectedSlide = useMemo(
    () => slides.find((slide) => slide.id === selectedSlideId) ?? null,
    [selectedSlideId, slides],
  );
  const selectedDialogue = selectedSlide ? dialogueBySlideId[selectedSlide.id] || "" : "";
  const currentSlideAvatarMeta = selectedSlideId ? (slideAvatarMeta[selectedSlideId] ?? null) : null;
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
  // AI credentials are now configured globally from the dashboard gear menu
  // ("Configuración" → "Credenciales de IA"). The backend readiness check
  // enforces credential presence (global provider credentials / environment),
  // so the editor no longer gates generation on per-project credentials.
  const hasAvatar =
    Boolean(projectAvatar) ||
    Boolean(videoSettings?.avatar_source_asset_id) ||
    Boolean(videoSettings?.using_debug_avatar_source);
  const generationHeartbeatFresh = isFreshHeartbeat(generationStatus.updated_at);
  const generationDisabledMessage = !hasAvatar
    ? "Upload an avatar image before generating video."
    : !allSlidesHaveDialogue
      ? "Every slide needs narration before video generation."
      : !allSlidesHavePreview
        ? "Every slide needs a rendered image preview before video generation."
        : isDirty
          ? "Save narration before generating video."
          : null;
  const canGenerateVideo = Boolean(
    hasAvatar &&
      allSlidesHaveDialogue &&
      allSlidesHavePreview &&
      !isDirty &&
      saveState !== "saving" &&
      !isGenerationRunning,
  );
  const generationFailureMessage = generationErrorMessage(generationStatus);
  const generationStatusText =
    isGenerationRunning &&
    generationStatus.status === "generating_avatar" &&
    generationHeartbeatFresh
      ? generationStatus.message || "Generating avatar... still processing"
      : generationStatus.message || "Idle";

  // ── load slides ──
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
        setSlideAvatarMeta(
          Object.fromEntries(data.map((slide) => [slide.id, extractSlideAvatarMeta(slide)])),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudieron cargar los slides.");
      } finally {
        setIsLoading(false);
      }
    }
    void loadSlides();
  }, [presentationId]);

  // ── load project/video state ──
  useEffect(() => {
    async function loadVideoExportState() {
      try {
        const [settings, status, avatar] = await Promise.all([
          api.videoSettings.get(params.projectId),
          api.generation.status(params.projectId),
          api.projects.getAvatar(params.projectId),
        ]);
        setVideoSettings(settings);
        setGenerationStatus(status);
        if (avatar) {
          setProjectAvatar(avatar);
          setAvatarPreviewUrl(avatar.avatar_preview_url);
          setAvatarUploadState("saved");
        }
      } catch (err) {
        setVideoError(
          err instanceof Error
            ? err.message
            : "No se pudo cargar la configuracion de video.",
        );
      }
    }
    void loadVideoExportState();
  }, [params.projectId]);

  // ── debug assets ──
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

  // ── poll generation status ──
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

  // ── cleanup blob URLs ──
  useEffect(() => {
    return () => {
      if (avatarPreviewUrl?.startsWith("blob:")) URL.revokeObjectURL(avatarPreviewUrl);
    };
  }, [avatarPreviewUrl]);

  // ── slide selection ──
  function selectSlide(slide: Slide) {
    setSelectedSlideId(slide.id);
    setSaveState("idle");
    setSaveError(null);
    setAvatarMetaSaveState("idle");
  }

  // ── narration ──
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
        metadata: { ...selectedSlide.metadata, dialogue: selectedDialogue },
      });
      setSlides((current) => current.map((slide) => (slide.id === updated.id ? updated : slide)));
      setDialogueBySlideId((current) => ({ ...current, [updated.id]: initialDialogue(updated) }));
      setSaveState("saved");
      setIsDirty(false);
    } catch (err) {
      setSaveState("error");
      setSaveError(err instanceof Error ? err.message : "No se pudo guardar el dialogo.");
    }
  }

  // ── project avatar upload ──
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
          ? { ...current, avatar_source_asset_id: uploaded.avatar_asset_id, avatar_source_url: null }
          : current,
      );
      setAvatarPreviewUrl(uploaded.avatar_preview_url);
      URL.revokeObjectURL(localPreviewUrl);
    } catch (err) {
      setAvatarUploadState("error");
      setAvatarUploadError(err instanceof Error ? err.message : "Avatar upload failed.");
    }
  }

  // ── per-slide avatar meta ──
  function updateCurrentSlideAvatarMeta(updated: SlideAvatarMeta) {
    if (!selectedSlideId) return;
    setSlideAvatarMeta((current) => ({ ...current, [selectedSlideId]: updated }));
    setAvatarMetaSaveState("idle");
  }

  async function saveCurrentSlideAvatarMeta() {
    if (!selectedSlide || !currentSlideAvatarMeta) return;
    setIsSavingSlideAvatarMeta(true);
    setAvatarMetaSaveState("idle");
    try {
      const patch = buildSlideAvatarPatch(selectedSlide, currentSlideAvatarMeta);
      const updated = await api.slides.update(selectedSlide.id, { metadata: patch });
      setSlides((current) => current.map((s) => (s.id === updated.id ? updated : s)));
      setAvatarMetaSaveState("saved");
    } catch {
      setAvatarMetaSaveState("error");
    } finally {
      setIsSavingSlideAvatarMeta(false);
    }
  }

  async function applyAvatarToAllSlides() {
    if (!currentSlideAvatarMeta || slides.length === 0) return;
    setIsApplyingToAll(true);
    try {
      const updatedSlides = await Promise.all(
        slides.map(async (slide) => {
          const patch = buildSlideAvatarPatch(slide, currentSlideAvatarMeta);
          return api.slides.update(slide.id, { metadata: patch });
        }),
      );
      // Update all slides in state and rebuild avatarMeta from server response
      setSlides(updatedSlides);
      setSlideAvatarMeta(
        Object.fromEntries(updatedSlides.map((s) => [s.id, extractSlideAvatarMeta(s)])),
      );
    } catch {
      // silent — user can retry
    } finally {
      setIsApplyingToAll(false);
    }
  }

  function resetToProjectDefault() {
    if (!selectedSlideId || !projectAvatar) return;
    setSlideAvatarMeta((current) => ({
      ...current,
      [selectedSlideId]: {
        x: projectAvatar.x,
        y: projectAvatar.y,
        width: projectAvatar.width,
        height: projectAvatar.height,
        borderRadius: projectAvatar.border_radius,
        fitMode: projectAvatar.fit_mode,
        visible: true,
      },
    }));
    setAvatarMetaSaveState("idle");
  }

  // ── avatar drag on canvas ──
  const handleAvatarDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!canvasRef.current || !selectedSlideId || !currentSlideAvatarMeta) return;
      // Capture non-null slideId for use inside closures
      const slideId = selectedSlideId;

      const rect = canvasRef.current.getBoundingClientRect();
      const startCanvasX = currentSlideAvatarMeta.x;
      const startCanvasY = currentSlideAvatarMeta.y;
      const startMouseX = e.clientX;
      const startMouseY = e.clientY;
      isDraggingRef.current = true;
      setIsDragging(true);

      function onMouseMove(ev: MouseEvent) {
        const dx = ev.clientX - startMouseX;
        const dy = ev.clientY - startMouseY;
        const newX = startCanvasX + (dx / rect.width) * CANVAS_W;
        const newY = startCanvasY + (dy / rect.height) * CANVAS_H;
        setSlideAvatarMeta((current) => {
          const meta = current[slideId];
          if (!meta) return current;
          return {
            ...current,
            [slideId]: {
              ...meta,
              x: clamp(newX, 0, CANVAS_W - meta.width),
              y: clamp(newY, 0, CANVAS_H - meta.height),
              fitMode: "custom",
            },
          };
        });
      }

      function onMouseUp() {
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
        isDraggingRef.current = false;
        setIsDragging(false);
        // Auto-save after drag — read latest state via callback form
        setSlideAvatarMeta((current) => {
          const meta = current[slideId];
          if (!meta) return current;
          void (async () => {
            const slide = slides.find((s) => s.id === slideId);
            if (!slide) return;
            try {
              const patch = buildSlideAvatarPatch(slide, meta);
              const updated = await api.slides.update(slide.id, { metadata: patch });
              setSlides((s) => s.map((sl) => (sl.id === updated.id ? updated : sl)));
            } catch {
              // silent
            }
          })();
          return current;
        });
      }

      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    },
    [currentSlideAvatarMeta, selectedSlideId, slides],
  );

  // ── avatar resize on canvas ──
  const handleAvatarResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!canvasRef.current || !selectedSlideId || !currentSlideAvatarMeta) return;
      const slideId = selectedSlideId;

      const rect = canvasRef.current.getBoundingClientRect();
      const startWidth = currentSlideAvatarMeta.width;
      const startHeight = currentSlideAvatarMeta.height;
      const startMouseX = e.clientX;
      const startMouseY = e.clientY;
      isResizingRef.current = true;
      setIsDragging(true);

      function onMouseMove(ev: MouseEvent) {
        const dw = ((ev.clientX - startMouseX) / rect.width) * CANVAS_W;
        const dh = ((ev.clientY - startMouseY) / rect.height) * CANVAS_H;
        setSlideAvatarMeta((current) => {
          const meta = current[slideId];
          if (!meta) return current;
          return {
            ...current,
            [slideId]: {
              ...meta,
              width: clamp(startWidth + dw, 20, CANVAS_W - meta.x),
              height: clamp(startHeight + dh, 20, CANVAS_H - meta.y),
              fitMode: "custom",
            },
          };
        });
      }

      function onMouseUp() {
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
        isResizingRef.current = false;
        setIsDragging(false);
        setSlideAvatarMeta((current) => {
          const meta = current[slideId];
          if (!meta) return current;
          void (async () => {
            const slide = slides.find((s) => s.id === slideId);
            if (!slide) return;
            try {
              const patch = buildSlideAvatarPatch(slide, meta);
              const updated = await api.slides.update(slide.id, { metadata: patch });
              setSlides((s) => s.map((sl) => (sl.id === updated.id ? updated : sl)));
            } catch {
              // silent
            }
          })();
          return current;
        });
      }

      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    },
    [currentSlideAvatarMeta, selectedSlideId, slides],
  );

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

  // ── render ────────────────────────────────────────────────────────────────

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
            Revisa los slides renderizados, configura narracion, avatar y credenciales, y genera el
            video.
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
          {/* ── Left: Slide list ── */}
          <aside className="rounded-lg border border-gray-200 bg-white shadow-sm">
            <div className="border-b border-gray-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-gray-900">Slides</h3>
            </div>
            <div className="max-h-[760px] overflow-auto">
              {slides.map((slide) => {
                const isSelected = slide.id === selectedSlideId;
                const imageUrl = slidePreviewUrl(slide);
                const avatarMeta = slideAvatarMeta[slide.id];
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
                      <div className="flex items-center gap-1.5">
                        {avatarMeta && !avatarMeta.visible && (
                          <span className="text-xs font-medium text-gray-400" title="Avatar oculto en esta lámina">
                            sin avatar
                          </span>
                        )}
                        {imageUrl ? (
                          <span className="text-xs font-medium text-green-700">Preview</span>
                        ) : (
                          <span className="text-xs font-medium text-amber-700">No preview</span>
                        )}
                      </div>
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

          {/* ── Center: Canvas + Narration ── */}
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
                  <div className="flex items-center gap-2">
                    {currentSlideAvatarMeta && !currentSlideAvatarMeta.visible && (
                      <span className="rounded-full bg-gray-100 px-2 py-1 text-xs font-medium text-gray-500">
                        Avatar oculto
                      </span>
                    )}
                    <span className="rounded-full bg-gray-100 px-2 py-1 text-xs font-medium text-gray-700">
                      {avatarPreviewUrl ? "Arrastra el avatar para reposicionarlo" : "Sin avatar subido"}
                    </span>
                  </div>
                </div>

                {/* Slide canvas with draggable avatar overlay */}
                <div className="rounded-lg border border-gray-200 bg-gray-100 p-3">
                  <div
                    ref={canvasRef}
                    className="relative mx-auto aspect-video w-full overflow-hidden rounded-md bg-white"
                    style={{ cursor: isDragging ? "grabbing" : "default" }}
                  >
                    {/* Slide background image */}
                    {slidePreviewUrl(selectedSlide) ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={slidePreviewUrl(selectedSlide)!}
                        alt={`Slide ${selectedSlide.position} rendered preview`}
                        className="absolute inset-0 h-full w-full rounded-md object-fill"
                        draggable={false}
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center text-sm text-amber-700">
                        No rendered slide preview is available.
                      </div>
                    )}

                    {/* Draggable avatar overlay */}
                    {avatarPreviewUrl &&
                      currentSlideAvatarMeta &&
                      currentSlideAvatarMeta.visible && (
                        <div
                          style={{
                            position: "absolute",
                            left: `${(currentSlideAvatarMeta.x / CANVAS_W) * 100}%`,
                            top: `${(currentSlideAvatarMeta.y / CANVAS_H) * 100}%`,
                            width: `${(currentSlideAvatarMeta.width / CANVAS_W) * 100}%`,
                            height: `${(currentSlideAvatarMeta.height / CANVAS_H) * 100}%`,
                            cursor: isDragging ? "grabbing" : "grab",
                            userSelect: "none",
                          }}
                          onMouseDown={handleAvatarDragStart}
                        >
                          {/* Avatar image */}
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={avatarPreviewUrl}
                            alt="Avatar"
                            draggable={false}
                            className="h-full w-full object-cover"
                            style={{
                              borderRadius: `${currentSlideAvatarMeta.borderRadius}%`,
                              pointerEvents: "none",
                            }}
                          />
                          {/* Selection border */}
                          <div
                            className="pointer-events-none absolute inset-0 border-2 border-indigo-500"
                            style={{ borderRadius: `${currentSlideAvatarMeta.borderRadius}%` }}
                          />
                          {/* Resize handle — bottom-right corner */}
                          <div
                            className="absolute bottom-0 right-0 h-3.5 w-3.5 cursor-se-resize rounded-sm bg-indigo-500 opacity-80 hover:opacity-100"
                            onMouseDown={handleAvatarResizeStart}
                          />
                        </div>
                      )}

                    {/* Hidden avatar badge */}
                    {currentSlideAvatarMeta && !currentSlideAvatarMeta.visible && (
                      <div className="absolute inset-0 flex items-center justify-center bg-black/10">
                        <span className="rounded-md bg-black/60 px-3 py-1.5 text-xs font-semibold text-white">
                          Avatar oculto en esta lámina
                        </span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Avatar meta save feedback (after drag or panel save) */}
                <div className="mt-2 min-h-4">
                  {avatarMetaSaveState === "saved" && (
                    <p className="text-xs font-medium text-green-700">
                      Configuración del avatar guardada.
                    </p>
                  )}
                  {avatarMetaSaveState === "error" && (
                    <p className="text-xs font-medium text-red-700">
                      No se pudo guardar la configuración del avatar.
                    </p>
                  )}
                </div>
              </section>
            ) : null}

            {/* Narration form */}
            {selectedSlide ? (
              <form
                onSubmit={saveSelectedDialogue}
                className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
              >
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-gray-900">Narration</h3>
                    <p className="mt-1 text-xs text-gray-500">
                      This text is sent to Wavespeed for the talking avatar clip. It does not
                      change the slide image.
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
                  {saveError ? (
                    <p className="text-xs font-medium text-red-700">{saveError}</p>
                  ) : null}
                </div>
              </form>
            ) : null}
          </main>

          {/* ── Right: Avatar settings + Video settings + Generation ── */}
          <aside className="space-y-5">
            {/* Avatar settings — inline collapsible accordion */}
            <CollapsibleSection
              title="Configuraciones del avatar"
              defaultOpen
              icon={
                <svg
                  className="h-4 w-4 text-brand-600"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <circle cx="12" cy="8" r="4" />
                  <path d="M4 21c0-4 4-6 8-6s8 2 8 6" />
                </svg>
              }
            >
              <AvatarSettingsPanel
                variant="bare"
                projectAvatar={projectAvatar}
                avatarPreviewUrl={avatarPreviewUrl}
                avatarUploadState={avatarUploadState}
                avatarUploadError={avatarUploadError}
                onAvatarFileChange={handleAvatarFile}
                slideMeta={currentSlideAvatarMeta}
                onSlideMetaChange={updateCurrentSlideAvatarMeta}
                onSaveSlideAvatarMeta={saveCurrentSlideAvatarMeta}
                isSavingSlideAvatarMeta={isSavingSlideAvatarMeta}
                onApplyToAllSlides={applyAvatarToAllSlides}
                isApplyingToAll={isApplyingToAll}
                onResetToProjectDefault={resetToProjectDefault}
                hasSlide={Boolean(selectedSlide)}
              />
            </CollapsibleSection>


            {/* Generation progress */}
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
              {generationDisabledMessage &&
              !videoError &&
              generationStatus.status === "idle" ? (
                <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700">
                  {generationDisabledMessage}
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
                  <span>{generationStatusText}</span>
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
