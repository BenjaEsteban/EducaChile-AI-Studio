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
import { VideoSettingsPanel } from "@/components/editor/VideoSettingsPanel";
// Note: the editor intentionally does NOT use AppShell (no global sidebar);
// it renders its own full-width top navbar + a 3-column workspace.
import {
  CANVAS_H,
  CANVAS_W,
  DebugGenerationAsset,
  DEFAULT_AVATAR_BORDER_WIDTH,
  DEFAULT_SLIDE_AVATAR,
  DEFAULT_SUBTITLE_BOX,
  GenerationStatus,
  MediaSettings,
  ProjectAvatar,
  Slide,
  SlideAvatarMeta,
  SlideSubtitleBox,
  VideoSettings,
  api,
  buildSlideAvatarPatch,
  buildSlideSubtitleBoxPatch,
  extractSlideAvatarMeta,
  extractSlideSubtitleBox,
} from "@/lib/api";

// ── helpers ──────────────────────────────────────────────────────────────────

type SaveState = "idle" | "saving" | "saved" | "error";


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

  // ── subtitle safe-area box (per-slide) ──
  const [slideSubtitleBox, setSlideSubtitleBox] = useState<Record<string, SlideSubtitleBox>>({});
  const [subtitleBoxIsCustom, setSubtitleBoxIsCustom] = useState<Record<string, boolean>>({});
  const [isSavingSubtitleBox, setIsSavingSubtitleBox] = useState(false);
  const [subtitleBoxSaveState, setSubtitleBoxSaveState] = useState<"idle" | "saved" | "error">(
    "idle",
  );
  const isDraggingSubtitleBoxRef = useRef(false);
  const isResizingSubtitleBoxRef = useRef(false);
  const [isDraggingSubtitleBox, setIsDraggingSubtitleBox] = useState(false);

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

  // ── media settings (background music + subtitles) ──
  const [isSavingMedia, setIsSavingMedia] = useState(false);
  const [mediaSaveState, setMediaSaveState] = useState<"idle" | "saved" | "error">("idle");
  const [mediaSaveError, setMediaSaveError] = useState<string | null>(null);
  const [musicState, setMusicState] = useState<"idle" | "uploading" | "saved" | "error">("idle");
  const [musicError, setMusicError] = useState<string | null>(null);
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
  // Displayed canvas width (px) so the avatar border can be rendered with a
  // thickness proportional to the export (which scales canvas units → output px).
  const [canvasDisplayWidth, setCanvasDisplayWidth] = useState(0);

  // ── derived ──
  const selectedSlide = useMemo(
    () => slides.find((slide) => slide.id === selectedSlideId) ?? null,
    [selectedSlideId, slides],
  );
  const selectedDialogue = selectedSlide ? dialogueBySlideId[selectedSlide.id] || "" : "";
  const currentSlideAvatarMeta = selectedSlideId ? (slideAvatarMeta[selectedSlideId] ?? null) : null;
  const currentSubtitleBox = selectedSlideId
    ? (slideSubtitleBox[selectedSlideId] ?? DEFAULT_SUBTITLE_BOX)
    : null;
  const currentSubtitleBoxIsCustom = selectedSlideId
    ? Boolean(subtitleBoxIsCustom[selectedSlideId])
    : false;
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
        const subtitleBoxEntries = data.map((slide) => [slide.id, extractSlideSubtitleBox(slide)] as const);
        setSlideSubtitleBox(
          Object.fromEntries(subtitleBoxEntries.map(([id, entry]) => [id, entry.box])),
        );
        setSubtitleBoxIsCustom(
          Object.fromEntries(subtitleBoxEntries.map(([id, entry]) => [id, entry.isCustom])),
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

  // Track the displayed canvas width to render a proportional avatar border.
  useEffect(() => {
    const el = canvasRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w) setCanvasDisplayWidth(w);
    });
    observer.observe(el);
    setCanvasDisplayWidth(el.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, [selectedSlideId, isLoading]);

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
        offsetY: 0,
        borderColor: null,
        borderWidth: DEFAULT_AVATAR_BORDER_WIDTH,
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

  // ── subtitle box drag/resize on canvas (mirrors the avatar box handlers) ──
  function persistSubtitleBox(slideId: string, box: SlideSubtitleBox) {
    void (async () => {
      const slide = slides.find((s) => s.id === slideId);
      if (!slide) return;
      try {
        const patch = buildSlideSubtitleBoxPatch(slide, box);
        const updated = await api.slides.update(slide.id, { metadata: patch });
        setSlides((s) => s.map((sl) => (sl.id === updated.id ? updated : sl)));
        setSubtitleBoxIsCustom((current) => ({ ...current, [slideId]: true }));
      } catch {
        // silent — user can retry via the explicit "Guardar" button
      }
    })();
  }

  const handleSubtitleBoxDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!canvasRef.current || !selectedSlideId || !currentSubtitleBox) return;
      const slideId = selectedSlideId;

      const rect = canvasRef.current.getBoundingClientRect();
      const startCanvasX = currentSubtitleBox.x;
      const startCanvasY = currentSubtitleBox.y;
      const startMouseX = e.clientX;
      const startMouseY = e.clientY;
      isDraggingSubtitleBoxRef.current = true;
      setIsDraggingSubtitleBox(true);

      function onMouseMove(ev: MouseEvent) {
        const dx = ev.clientX - startMouseX;
        const dy = ev.clientY - startMouseY;
        const newX = startCanvasX + (dx / rect.width) * CANVAS_W;
        const newY = startCanvasY + (dy / rect.height) * CANVAS_H;
        setSlideSubtitleBox((current) => {
          const box = current[slideId];
          if (!box) return current;
          return {
            ...current,
            [slideId]: {
              ...box,
              x: clamp(newX, 0, CANVAS_W - box.width),
              y: clamp(newY, 0, CANVAS_H - box.height),
            },
          };
        });
      }

      function onMouseUp() {
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
        isDraggingSubtitleBoxRef.current = false;
        setIsDraggingSubtitleBox(false);
        setSlideSubtitleBox((current) => {
          const box = current[slideId];
          if (box) persistSubtitleBox(slideId, box);
          return current;
        });
      }

      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    },
    [currentSubtitleBox, selectedSlideId, slides],
  );

  const handleSubtitleBoxResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!canvasRef.current || !selectedSlideId || !currentSubtitleBox) return;
      const slideId = selectedSlideId;

      const rect = canvasRef.current.getBoundingClientRect();
      const startWidth = currentSubtitleBox.width;
      const startHeight = currentSubtitleBox.height;
      const startMouseX = e.clientX;
      const startMouseY = e.clientY;
      isResizingSubtitleBoxRef.current = true;
      setIsDraggingSubtitleBox(true);

      function onMouseMove(ev: MouseEvent) {
        const dw = ((ev.clientX - startMouseX) / rect.width) * CANVAS_W;
        const dh = ((ev.clientY - startMouseY) / rect.height) * CANVAS_H;
        setSlideSubtitleBox((current) => {
          const box = current[slideId];
          if (!box) return current;
          return {
            ...current,
            [slideId]: {
              ...box,
              width: clamp(startWidth + dw, 60, CANVAS_W - box.x),
              height: clamp(startHeight + dh, 30, CANVAS_H - box.y),
            },
          };
        });
      }

      function onMouseUp() {
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
        isResizingSubtitleBoxRef.current = false;
        setIsDraggingSubtitleBox(false);
        setSlideSubtitleBox((current) => {
          const box = current[slideId];
          if (box) persistSubtitleBox(slideId, box);
          return current;
        });
      }

      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    },
    [currentSubtitleBox, selectedSlideId, slides],
  );

  function updateCurrentSubtitleBox(box: SlideSubtitleBox) {
    if (!selectedSlideId) return;
    setSlideSubtitleBox((current) => ({ ...current, [selectedSlideId]: box }));
  }

  async function saveCurrentSubtitleBox() {
    if (!selectedSlideId || !currentSubtitleBox) return;
    setIsSavingSubtitleBox(true);
    setSubtitleBoxSaveState("idle");
    try {
      const slide = slides.find((s) => s.id === selectedSlideId);
      if (!slide) return;
      const patch = buildSlideSubtitleBoxPatch(slide, currentSubtitleBox);
      const updated = await api.slides.update(slide.id, { metadata: patch });
      setSlides((s) => s.map((sl) => (sl.id === updated.id ? updated : sl)));
      setSubtitleBoxIsCustom((current) => ({ ...current, [selectedSlideId]: true }));
      setSubtitleBoxSaveState("saved");
    } catch {
      setSubtitleBoxSaveState("error");
    } finally {
      setIsSavingSubtitleBox(false);
    }
  }

  async function resetCurrentSubtitleBox() {
    if (!selectedSlideId) return;
    const slide = slides.find((s) => s.id === selectedSlideId);
    if (!slide) return;
    try {
      const patch = buildSlideSubtitleBoxPatch(slide, null);
      const updated = await api.slides.update(slide.id, { metadata: patch });
      setSlides((s) => s.map((sl) => (sl.id === updated.id ? updated : sl)));
      setSlideSubtitleBox((current) => ({ ...current, [selectedSlideId]: { ...DEFAULT_SUBTITLE_BOX } }));
      setSubtitleBoxIsCustom((current) => ({ ...current, [selectedSlideId]: false }));
      setSubtitleBoxSaveState("idle");
    } catch {
      setSubtitleBoxSaveState("error");
    }
  }

  async function saveMediaSettings(media: MediaSettings) {
    setIsSavingMedia(true);
    setMediaSaveState("idle");
    setMediaSaveError(null);
    try {
      const updated = await api.videoSettings.update(params.projectId, {
        media_settings: {
          background_music: {
            loop: media.background_music.loop,
            volume: media.background_music.volume,
            fade_out_enabled: media.background_music.fade_out_enabled,
            fade_out_seconds: media.background_music.fade_out_seconds,
          },
          subtitles: media.subtitles,
        },
      });
      setVideoSettings(updated);
      setMediaSaveState("saved");
    } catch (err) {
      setMediaSaveState("error");
      setMediaSaveError(
        err instanceof Error ? err.message : "No se pudo guardar la configuración del video.",
      );
    } finally {
      setIsSavingMedia(false);
    }
  }

  async function handleMusicFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setMusicState("uploading");
    setMusicError(null);
    try {
      const updated = await api.videoSettings.uploadBackgroundMusic(params.projectId, file);
      setVideoSettings(updated);
      setMusicState("saved");
    } catch (err) {
      setMusicState("error");
      setMusicError(err instanceof Error ? err.message : "No se pudo subir la música.");
    } finally {
      event.target.value = "";
    }
  }

  async function deleteMusic() {
    try {
      const updated = await api.videoSettings.deleteBackgroundMusic(params.projectId);
      setVideoSettings(updated);
      setMusicState("idle");
    } catch (err) {
      setMusicState("error");
      setMusicError(err instanceof Error ? err.message : "No se pudo quitar la música.");
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

  // ── render ────────────────────────────────────────────────────────────────

  // Generation panel content, reused inside the navbar "Generar video" popover.
  const generationPanel = (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-gray-900">Generación del video</h3>

      {!allSlidesHavePreview ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Cada lámina necesita una imagen renderizada antes de generar el video.
        </div>
      ) : null}
      {!allSlidesHaveDialogue ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Cada lámina necesita narración antes de generar el video.
        </div>
      ) : null}
      {isDirty ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Guarda la narración antes de generar el video.
        </div>
      ) : null}
      {!hasAvatar ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Sube una imagen de avatar antes de generar el video.
        </div>
      ) : null}
      {generationDisabledMessage && !videoError && generationStatus.status === "idle" ? (
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
            Lámina {generationStatus.current_slide} de {generationStatus.total_slides}
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
          ? "Iniciando..."
          : isGenerationRunning
            ? generationStatus.message || "Generando..."
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
            Descargar MP4
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
    </div>
  );

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Top navbar — the editor's only chrome (no sidebar). Navigation +
          main actions (avatar config, generation) live here. */}
      <header className="sticky top-0 z-40 border-b border-slate-200/70 bg-white/90 backdrop-blur">
        <div className="mx-auto flex min-h-16 w-full max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-2 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              href="/"
              className="truncate text-sm font-semibold tracking-tight text-slate-900 sm:text-base"
            >
              EducaChile
            </Link>
            <span className="hidden text-xs text-slate-400 sm:inline">/</span>
            <span className="hidden text-xs font-medium uppercase tracking-wide text-brand-700 sm:inline">
              Editor
            </span>
            <Link
              href="/dashboard"
              className="ml-1 inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
            >
              <svg
                className="h-4 w-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="m15 18-6-6 6-6" />
              </svg>
              Mis videos
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
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
        <div className="grid gap-5 lg:grid-cols-[240px_minmax(0,1fr)] xl:grid-cols-[240px_minmax(0,1fr)_360px]">
          {/* ── Left column: vertical slide list ("Láminas") ── */}
          <aside className="rounded-lg border border-gray-200 bg-white shadow-sm lg:sticky lg:top-20 lg:self-start">
            <div className="flex items-center justify-between border-b border-gray-200 px-3 py-2.5">
              <h3 className="text-sm font-semibold text-gray-900">Láminas</h3>
              <span className="text-xs text-gray-500">{slides.length}</span>
            </div>
            <div className="flex max-h-[70vh] flex-col gap-2 overflow-y-auto p-2">
              {slides.map((slide) => {
                const isSelected = slide.id === selectedSlideId;
                const imageUrl = slidePreviewUrl(slide);
                const avatarMeta = slideAvatarMeta[slide.id];
                return (
                  <button
                    key={slide.id}
                    type="button"
                    onClick={() => selectSlide(slide)}
                    title={slide.title || `Lámina ${slide.position}`}
                    className={`group w-full rounded-md border p-1.5 text-left transition-colors ${
                      isSelected
                        ? "border-brand-500 bg-brand-50 ring-1 ring-brand-300"
                        : "border-gray-200 hover:bg-gray-50"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium uppercase text-gray-500">
                        Lámina {slide.position}
                      </span>
                      {avatarMeta && !avatarMeta.visible ? (
                        <span className="text-[10px] font-medium text-gray-400" title="Avatar oculto en esta lámina">
                          sin avatar
                        </span>
                      ) : imageUrl ? null : (
                        <span className="text-[10px] font-medium text-amber-700">sin preview</span>
                      )}
                    </div>
                    {imageUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={imageUrl}
                        alt={`Lámina ${slide.position}`}
                        className="mt-1 aspect-video w-full rounded border border-gray-200 object-contain"
                      />
                    ) : (
                      <div className="mt-1 flex aspect-video w-full items-center justify-center rounded border border-dashed border-gray-300 bg-gray-50 text-[10px] text-gray-400">
                        Sin preview
                      </div>
                    )}
                    <p className="mt-1 truncate text-xs font-semibold text-gray-900">
                      {slide.title || "Sin título"}
                    </p>
                  </button>
                );
              })}
            </div>
          </aside>

          {/* ── Center column: PPT / slide canvas + narration ── */}
          <div className="min-w-0 space-y-5">
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
                        No hay una imagen renderizada disponible para esta lámina.
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
                            // offsetY: vertical fine-tune (full-slide face centering),
                            // applied the same way the export shifts the overlay.
                            top: `${((currentSlideAvatarMeta.y + currentSlideAvatarMeta.offsetY) / CANVAS_H) * 100}%`,
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
                          {/* Configured avatar frame border (persisted, used in export).
                              Width is rendered proportional to the displayed canvas so it
                              matches the exported thickness (canvas units → output px). */}
                          {currentSlideAvatarMeta.borderColor ? (
                            <div
                              className="pointer-events-none absolute inset-0"
                              style={{
                                borderRadius: `${currentSlideAvatarMeta.borderRadius}%`,
                                border: `${Math.max(
                                  2,
                                  Math.round(
                                    currentSlideAvatarMeta.borderWidth *
                                      ((canvasDisplayWidth || CANVAS_W) / CANVAS_W),
                                  ),
                                )}px solid ${currentSlideAvatarMeta.borderColor}`,
                              }}
                            />
                          ) : null}
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

                    {/* Subtitle safe-area box — draggable/resizable, mirrors the avatar box.
                        Only shown when subtitles are enabled for the project. */}
                    {currentSubtitleBox &&
                      videoSettings?.media_settings?.subtitles.enabled !== false && (
                        <div
                          style={{
                            position: "absolute",
                            left: `${(currentSubtitleBox.x / CANVAS_W) * 100}%`,
                            top: `${(currentSubtitleBox.y / CANVAS_H) * 100}%`,
                            width: `${(currentSubtitleBox.width / CANVAS_W) * 100}%`,
                            height: `${(currentSubtitleBox.height / CANVAS_H) * 100}%`,
                            cursor: isDraggingSubtitleBox ? "grabbing" : "grab",
                            userSelect: "none",
                          }}
                          onMouseDown={handleSubtitleBoxDragStart}
                        >
                          <div className="flex h-full w-full items-center justify-center rounded-sm border-2 border-dashed border-amber-500 bg-amber-500/10">
                            <span className="pointer-events-none select-none rounded bg-amber-500/90 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                              Área de subtítulos
                            </span>
                          </div>
                          <div
                            className="absolute bottom-0 right-0 h-3.5 w-3.5 cursor-se-resize rounded-sm bg-amber-500 opacity-80 hover:opacity-100"
                            onMouseDown={handleSubtitleBoxResizeStart}
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
                    <h3 className="text-sm font-semibold text-gray-900">Narración</h3>
                    <p className="mt-1 text-xs text-gray-500">
                      Este texto se envía a Wavespeed para el clip del avatar parlante. No
                      modifica la imagen de la lámina.
                    </p>
                  </div>
                  <button
                    type="submit"
                    disabled={saveState === "saving" || !selectedSlide}
                    className="rounded-md bg-brand-600 px-3 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-400"
                  >
                    {saveState === "saving" ? "Guardando..." : "Guardar narración"}
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
                    <p className="text-xs font-medium text-green-700">Narración guardada.</p>
                  ) : null}
                  {saveError ? (
                    <p className="text-xs font-medium text-red-700">{saveError}</p>
                  ) : null}
                </div>
              </form>
            ) : null}
          </div>

          {/* ── Right column: avatar settings + video generation ── */}
          <aside className="space-y-5 xl:sticky xl:top-20 xl:self-start">
            <CollapsibleSection
              title="Configuración de avatar"
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

            <CollapsibleSection
              title="Configuración del video"
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
                  <path d="M9 18V5l12-2v13" />
                  <circle cx="6" cy="18" r="3" />
                  <circle cx="18" cy="16" r="3" />
                </svg>
              }
            >
              <VideoSettingsPanel
                videoSettings={videoSettings}
                onSaveMediaSettings={saveMediaSettings}
                isSaving={isSavingMedia}
                saveState={mediaSaveState}
                saveError={mediaSaveError}
                onUploadMusic={handleMusicFile}
                onDeleteMusic={deleteMusic}
                musicState={musicState}
                musicError={musicError}
                hasSlide={Boolean(selectedSlide)}
                subtitleBox={currentSubtitleBox}
                isSubtitleBoxCustom={currentSubtitleBoxIsCustom}
                onSubtitleBoxChange={updateCurrentSubtitleBox}
                onSaveSubtitleBox={saveCurrentSubtitleBox}
                onResetSubtitleBox={resetCurrentSubtitleBox}
                isSavingSubtitleBox={isSavingSubtitleBox}
                subtitleBoxSaveState={subtitleBoxSaveState}
              />
            </CollapsibleSection>

            <CollapsibleSection
              title="Generar video"
              defaultOpen
              badge={
                <span
                  className={`inline-block h-2 w-2 rounded-full ${
                    generationStatus.status === "completed"
                      ? "bg-green-500"
                      : generationStatus.status === "failed"
                        ? "bg-red-500"
                        : isGenerationRunning
                          ? "bg-amber-500"
                          : "bg-gray-300"
                  }`}
                />
              }
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
                  <path d="m22 8-6 4 6 4V8Z" />
                  <rect x="2" y="6" width="14" height="12" rx="2" />
                </svg>
              }
            >
              <div className="p-4">{generationPanel}</div>
            </CollapsibleSection>
          </aside>
        </div>
      )}
      </main>
    </div>
  );
}
