"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  ChangeEvent,
  CSSProperties,
  FormEvent,
  PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { AppShell } from "@/components/layout/AppShell";
import { GenerationStatus, Slide, VideoSettings, api } from "@/lib/api";

type SaveState = "idle" | "saving" | "saved" | "error";

interface AvatarLayout {
  enabled: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
}

interface TextBlockLayout {
  id: string;
  type: "title" | "body";
  shapeIndex?: number;
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  rotation?: number;
  zIndex: number;
  fontFamily: string;
  originalFontFamily: string | null;
  fallbackFontFamily: string;
  fontSize: number;
  fontWeight: "400" | "600" | "700";
  color: string;
  originalColor: string | null;
  bold: boolean;
  italic: boolean;
  underline: boolean;
  textAlign: "left" | "center" | "right" | "justify";
  lineHeight: number;
  letterSpacing: number;
  backgroundColor: string;
}

interface CanvasLayout {
  width: number;
  height: number;
  avatar: AvatarLayout;
  text: {
    title: string;
    visible_text: string;
  };
  textBlocks: TextBlockLayout[];
}

interface SlideDraft {
  title: string;
  visibleText: string;
  dialogue: string;
  canvasWidth: number;
  canvasHeight: number;
  avatar: AvatarLayout;
  textBlocks: TextBlockLayout[];
}

const DEFAULT_CANVAS_WIDTH = 960;
const DEFAULT_CANVAS_HEIGHT = 540;
const DEFAULT_AVATAR: AvatarLayout = {
  enabled: true,
  x: 700,
  y: 290,
  width: 200,
  height: 200,
};

const DEFAULT_TITLE_BLOCK: Omit<TextBlockLayout, "text"> = {
  id: "title-0",
  type: "title",
  x: 72,
  y: 64,
  width: 620,
  height: 96,
  zIndex: 10,
  fontFamily: "Arial",
  originalFontFamily: null,
  fallbackFontFamily: "Arial",
  fontSize: 34,
  fontWeight: "700",
  color: "#111827",
  originalColor: "#111827",
  bold: true,
  italic: false,
  underline: false,
  textAlign: "left",
  lineHeight: 1.15,
  letterSpacing: 0,
  backgroundColor: "transparent",
};

const DEFAULT_BODY_BLOCK: Omit<TextBlockLayout, "text"> = {
  id: "body-1",
  type: "body",
  x: 72,
  y: 180,
  width: 560,
  height: 260,
  zIndex: 11,
  fontFamily: "Arial",
  originalFontFamily: null,
  fallbackFontFamily: "Arial",
  fontSize: 22,
  fontWeight: "400",
  color: "#1f2937",
  originalColor: "#1f2937",
  bold: false,
  italic: false,
  underline: false,
  textAlign: "left",
  lineHeight: 1.15,
  letterSpacing: 0,
  backgroundColor: "transparent",
};

function preview(value: string | null | undefined) {
  if (!value) return "Sin contenido";
  return value.length > 90 ? `${value.slice(0, 90)}...` : value;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function normalizeAvatar(
  avatar: AvatarLayout,
  canvasWidth = DEFAULT_CANVAS_WIDTH,
  canvasHeight = DEFAULT_CANVAS_HEIGHT,
): AvatarLayout {
  const width = clamp(Number(avatar.width) || DEFAULT_AVATAR.width, 80, canvasWidth);
  const height = clamp(Number(avatar.height) || DEFAULT_AVATAR.height, 80, canvasHeight);
  return {
    enabled: Boolean(avatar.enabled),
    width,
    height,
    x: clamp(Number(avatar.x) || 0, 0, canvasWidth - width),
    y: clamp(Number(avatar.y) || 0, 0, canvasHeight - height),
  };
}

function normalizeTextBlock(
  block: TextBlockLayout,
  canvasWidth = DEFAULT_CANVAS_WIDTH,
  canvasHeight = DEFAULT_CANVAS_HEIGHT,
): TextBlockLayout {
  const width = clamp(Number(block.width) || DEFAULT_BODY_BLOCK.width, 80, canvasWidth);
  const height = clamp(Number(block.height) || DEFAULT_BODY_BLOCK.height, 48, canvasHeight);
  return {
    id: block.id || `text-${block.type}`,
    type: block.type === "title" ? "title" : "body",
    shapeIndex: block.shapeIndex,
    text: block.text || "",
    width,
    height,
    x: clamp(Number(block.x) || 0, 0, canvasWidth - width),
    y: clamp(Number(block.y) || 0, 0, canvasHeight - height),
    rotation: Number(block.rotation) || 0,
    zIndex: Number(block.zIndex) || (block.type === "title" ? 10 : 11),
    fontFamily: block.fontFamily || block.originalFontFamily || "Arial",
    originalFontFamily: block.originalFontFamily || null,
    fallbackFontFamily: block.fallbackFontFamily || "Arial",
    fontSize: clamp(Number(block.fontSize) || DEFAULT_BODY_BLOCK.fontSize, 12, 64),
    fontWeight: ["400", "600", "700"].includes(block.fontWeight)
      ? block.fontWeight
      : "400",
    color: block.color || block.originalColor || "#111827",
    originalColor: block.originalColor || block.color || null,
    bold: Boolean(block.bold),
    italic: Boolean(block.italic),
    underline: Boolean(block.underline),
    textAlign: ["left", "center", "right", "justify"].includes(block.textAlign)
      ? block.textAlign
      : "left",
    lineHeight: Number(block.lineHeight) || 1.15,
    letterSpacing: Number(block.letterSpacing) || 0,
    backgroundColor: block.backgroundColor || "transparent",
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function getBackgroundImageUrl(slide: Slide): string | null {
  return (
    slide.background_image_url ||
    getString(slide.metadata.background_image_url) ||
    getString(slide.metadata.canvas_background_url) ||
    null
  );
}

function getDefaultTextBlocks(title: string, visibleText: string): TextBlockLayout[] {
  const blocks: TextBlockLayout[] = [];
  if (title.trim()) {
    blocks.push(normalizeTextBlock({ ...DEFAULT_TITLE_BLOCK, text: title }));
  }
  if (visibleText.trim()) {
    blocks.push(normalizeTextBlock({ ...DEFAULT_BODY_BLOCK, text: visibleText }));
  }
  if (blocks.length === 0) {
    blocks.push(normalizeTextBlock({ ...DEFAULT_BODY_BLOCK, text: "" }));
  }
  return blocks;
}

function getTextBlockString(block: Record<string, unknown>, key: string): string | null {
  const value = block[key];
  return typeof value === "string" ? value : null;
}

function getTextBlockNumber(block: Record<string, unknown>, key: string): number | null {
  const value = block[key];
  return typeof value === "number" ? value : null;
}

function getCanvasNumber(canvas: Record<string, unknown>, key: string, fallback: number): number {
  const value = canvas[key];
  return typeof value === "number" && value > 0 ? value : fallback;
}

function getTextBlockFontWeight(block: Record<string, unknown>): TextBlockLayout["fontWeight"] {
  const value = getTextBlockString(block, "fontWeight");
  return value === "700" || value === "600" ? value : "400";
}

function getTextBlockAlign(block: Record<string, unknown>): TextBlockLayout["textAlign"] {
  const value = getTextBlockString(block, "textAlign");
  return value === "center" || value === "right" || value === "justify" ? value : "left";
}

function getTextBlockStyle(block: Record<string, unknown>): Record<string, unknown> {
  return isRecord(block.style) ? block.style : block;
}

function getElementRole(element: Record<string, unknown>): "title" | "body" {
  const role = getTextBlockString(element, "role") || getTextBlockString(element, "type");
  return role === "title" ? "title" : "body";
}

function getTextBlocksFromMetadata(
  canvas: Record<string, unknown>,
  title: string,
  visibleText: string,
  canvasWidth: number,
  canvasHeight: number,
): TextBlockLayout[] {
  const rawElements = Array.isArray(canvas.elements) ? canvas.elements : [];
  const rawBlocks = rawElements
    .filter(isRecord)
    .filter((element) => element.type === "text");
  const sourceBlocks = rawBlocks.length > 0
    ? rawBlocks
    : Array.isArray(canvas.text_blocks)
      ? canvas.text_blocks
      : [];
  const blocks = sourceBlocks
    .filter(isRecord)
    .map((block, index) => {
      const style = getTextBlockStyle(block);
      const role = rawBlocks.length > 0 ? getElementRole(block) : getElementRole(block);
      return normalizeTextBlock(
        {
          id: getTextBlockString(block, "id") || `text-${index}`,
          type: role,
          shapeIndex: getTextBlockNumber(block, "shape_index") ?? undefined,
          text: getTextBlockString(block, "text") || "",
          x: getTextBlockNumber(block, "x") ?? DEFAULT_BODY_BLOCK.x,
          y: getTextBlockNumber(block, "y") ?? DEFAULT_BODY_BLOCK.y,
          width: getTextBlockNumber(block, "width") ?? DEFAULT_BODY_BLOCK.width,
          height: getTextBlockNumber(block, "height") ?? DEFAULT_BODY_BLOCK.height,
          rotation: getTextBlockNumber(block, "rotation") ?? 0,
          zIndex: getTextBlockNumber(block, "zIndex") ?? 10 + index,
          fontFamily:
            getTextBlockString(style, "fontFamily") ||
            getTextBlockString(style, "originalFontFamily") ||
            "Arial",
          originalFontFamily: getTextBlockString(style, "originalFontFamily"),
          fallbackFontFamily: getTextBlockString(style, "fallbackFontFamily") || "Arial",
          fontSize: getTextBlockNumber(style, "fontSize") ?? DEFAULT_BODY_BLOCK.fontSize,
          fontWeight: getTextBlockFontWeight(style),
          color: getTextBlockString(style, "color") || DEFAULT_BODY_BLOCK.color,
          originalColor:
            getTextBlockString(style, "originalColor") ||
            getTextBlockString(style, "color"),
          bold: Boolean(style.bold),
          italic: Boolean(style.italic),
          underline: Boolean(style.underline),
          textAlign: getTextBlockAlign(style),
          lineHeight: getTextBlockNumber(style, "lineHeight") ?? 1.15,
          letterSpacing: getTextBlockNumber(style, "letterSpacing") ?? 0,
          backgroundColor:
            getTextBlockString(style, "backgroundColor") || "transparent",
        },
        canvasWidth,
        canvasHeight,
      );
    });

  return blocks.length > 0 ? blocks : getDefaultTextBlocks(title, visibleText);
}

function getCanvasLayout(slide: Slide): CanvasLayout {
  const canvas = isRecord(slide.metadata.canvas) ? slide.metadata.canvas : {};
  const avatar = isRecord(canvas.avatar) ? canvas.avatar : {};
  const text = isRecord(canvas.text) ? canvas.text : {};
  const width = getCanvasNumber(canvas, "width", DEFAULT_CANVAS_WIDTH);
  const height = getCanvasNumber(canvas, "height", DEFAULT_CANVAS_HEIGHT);
  const title = typeof text.title === "string" ? text.title : slide.title || "";
  const visibleText =
    typeof text.visible_text === "string" ? text.visible_text : slide.visible_text || "";

  return {
    width,
    height,
    avatar: normalizeAvatar(
      {
        enabled: typeof avatar.enabled === "boolean" ? avatar.enabled : DEFAULT_AVATAR.enabled,
        x: typeof avatar.x === "number" ? avatar.x : DEFAULT_AVATAR.x,
        y: typeof avatar.y === "number" ? avatar.y : DEFAULT_AVATAR.y,
        width: typeof avatar.width === "number" ? avatar.width : DEFAULT_AVATAR.width,
        height: typeof avatar.height === "number" ? avatar.height : DEFAULT_AVATAR.height,
      },
      width,
      height,
    ),
    text: {
      title,
      visible_text: visibleText,
    },
    textBlocks: getTextBlocksFromMetadata(canvas, title, visibleText, width, height),
  };
}

function makeDraft(slide: Slide): SlideDraft {
  const canvas = getCanvasLayout(slide);
  return {
    title: canvas.text.title,
    visibleText: canvas.text.visible_text,
    dialogue: slide.dialogue || slide.notes || "",
    canvasWidth: canvas.width,
    canvasHeight: canvas.height,
    avatar: canvas.avatar,
    textBlocks: canvas.textBlocks,
  };
}

function makeMetadata(slide: Slide, draft: SlideDraft): Record<string, unknown> {
  const backgroundKey = getString(slide.metadata.background_image_key);
  const backgroundElement = {
    id: "background",
    type: "background",
    x: 0,
    y: 0,
    width: draft.canvasWidth,
    height: draft.canvasHeight,
    zIndex: 0,
    src: backgroundKey,
  };
  const textElements = draft.textBlocks.map((block) => {
    const normalized = normalizeTextBlock(block, draft.canvasWidth, draft.canvasHeight);
    return {
      id: normalized.id,
      type: "text",
      role: normalized.type,
      shape_index: normalized.shapeIndex,
      x: normalized.x,
      y: normalized.y,
      width: normalized.width,
      height: normalized.height,
      rotation: normalized.rotation || 0,
      zIndex: normalized.zIndex,
      text: normalized.text,
      style: {
        fontFamily: normalized.fontFamily,
        originalFontFamily: normalized.originalFontFamily,
        fallbackFontFamily: normalized.fallbackFontFamily,
        fontSize: normalized.fontSize,
        fontWeight: normalized.fontWeight,
        color: normalized.color,
        originalColor: normalized.originalColor || normalized.color,
        bold: normalized.bold,
        italic: normalized.italic,
        underline: normalized.underline,
        textAlign: normalized.textAlign,
        lineHeight: normalized.lineHeight,
        letterSpacing: normalized.letterSpacing,
        backgroundColor: normalized.backgroundColor,
      },
    };
  });
  return {
    ...slide.metadata,
    visible_text: draft.visibleText,
    dialogue: draft.dialogue,
    canvas: {
      version: 1,
      width: draft.canvasWidth,
      height: draft.canvasHeight,
      avatar: normalizeAvatar(draft.avatar, draft.canvasWidth, draft.canvasHeight),
      background: {
        type: "background",
        key: backgroundKey,
      },
      text: {
        title: draft.title,
        visible_text: draft.visibleText,
      },
      text_blocks: draft.textBlocks.map((block) =>
        normalizeTextBlock(block, draft.canvasWidth, draft.canvasHeight),
      ),
      elements: [
        backgroundElement,
        ...textElements,
        {
          id: "avatar",
          type: "avatar",
          x: draft.avatar.x,
          y: draft.avatar.y,
          width: draft.avatar.width,
          height: draft.avatar.height,
          zIndex: 1000,
        },
      ],
    },
  };
}

function applyAvatarToMetadata(slide: Slide, avatar: AvatarLayout): Record<string, unknown> {
  const currentCanvas = getCanvasLayout(slide);
  return {
    ...slide.metadata,
    canvas: {
      version: 1,
      width: currentCanvas.width,
      height: currentCanvas.height,
      avatar: normalizeAvatar(avatar, currentCanvas.width, currentCanvas.height),
      text: currentCanvas.text,
      text_blocks: currentCanvas.textBlocks,
      elements: [
        ...(Array.isArray((slide.metadata.canvas as Record<string, unknown> | undefined)?.elements)
          ? ((slide.metadata.canvas as Record<string, unknown>).elements as unknown[])
              .filter(isRecord)
              .filter((element) => element.type !== "avatar")
          : []),
        {
          id: "avatar",
          type: "avatar",
          ...normalizeAvatar(avatar, currentCanvas.width, currentCanvas.height),
          zIndex: 1000,
        },
      ],
    },
  };
}

function toPercent(value: number, total: number) {
  return `${(value / total) * 100}%`;
}

function textBlockCss(block: TextBlockLayout): CSSProperties {
  const fontFamily = block.originalFontFamily || block.fontFamily || "Arial";
  const fallback = block.fallbackFontFamily || "Arial";
  const color = block.color || block.originalColor || "#000000";
  return {
    fontFamily: `"${fontFamily}", "${fallback}", Arial, Helvetica, sans-serif`,
    fontSize: `${block.fontSize}px`,
    fontWeight: block.fontWeight,
    color,
    caretColor: color,
    fontStyle: block.italic ? "italic" : "normal",
    textDecoration: block.underline ? "underline" : "none",
    textAlign: block.textAlign,
    lineHeight: block.lineHeight,
    letterSpacing: `${block.letterSpacing}px`,
    backgroundColor: block.backgroundColor,
  };
}

export default function ProjectEditorPage() {
  const params = useParams<{ projectId: string }>();
  const searchParams = useSearchParams();
  const presentationId = searchParams.get("presentationId");
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const [slides, setSlides] = useState<Slide[]>([]);
  const [selectedSlideId, setSelectedSlideId] = useState<string | null>(null);
  const [draft, setDraft] = useState<SlideDraft>({
    title: "",
    visibleText: "",
    dialogue: "",
    canvasWidth: DEFAULT_CANVAS_WIDTH,
    canvasHeight: DEFAULT_CANVAS_HEIGHT,
    avatar: DEFAULT_AVATAR,
    textBlocks: getDefaultTextBlocks("", ""),
  });
  const [draftsBySlideId, setDraftsBySlideId] = useState<Record<string, SlideDraft>>({});
  const [isLoading, setIsLoading] = useState(Boolean(presentationId));
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [isDirty, setIsDirty] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [avatarPreviewUrl, setAvatarPreviewUrl] = useState<string | null>(null);
  const [dragOffset, setDragOffset] = useState<{ x: number; y: number } | null>(null);
  const [textDrag, setTextDrag] = useState<{
    blockId: string;
    offsetX: number;
    offsetY: number;
  } | null>(null);
  const [selectedTextBlockId, setSelectedTextBlockId] = useState<string | null>(null);
  const [editingTextBlockId, setEditingTextBlockId] = useState<string | null>(null);
  const [videoSettings, setVideoSettings] = useState<VideoSettings | null>(null);
  const [elevenlabsApiKey, setElevenlabsApiKey] = useState("");
  const [elevenlabsVoiceId, setElevenlabsVoiceId] = useState("");
  const [wavespeedApiKey, setWavespeedApiKey] = useState("");
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

  const selectedSlide = useMemo(
    () => slides.find((slide) => slide.id === selectedSlideId) ?? null,
    [selectedSlideId, slides],
  );

  const selectedSlideBackgroundUrl = selectedSlide ? getBackgroundImageUrl(selectedSlide) : null;
  const allSlidesHaveDialogue = useMemo(
    () =>
      slides.length > 0 &&
      slides.every((slide) => {
        const draftForSlide =
          slide.id === selectedSlideId ? draft : draftsBySlideId[slide.id] || makeDraft(slide);
        return Boolean((draftForSlide.dialogue || slide.notes || "").trim());
      }),
    [draft, draftsBySlideId, selectedSlideId, slides],
  );
  const canGenerateVideo = Boolean(
    videoSettings?.elevenlabs_api_key_masked &&
      videoSettings.elevenlabs_voice_id &&
      videoSettings.wavespeed_api_key_masked &&
      videoSettings.elevenlabs_valid &&
      videoSettings.wavespeed_valid &&
      allSlidesHaveDialogue &&
      !isDirty &&
      saveState !== "saving" &&
      !["queued", "validating", "generating_audio", "generating_avatar", "rendering_slides", "composing_video"].includes(
        generationStatus.status,
      ),
  );

  useEffect(() => {
    async function loadSlides() {
      if (!presentationId) return;

      try {
        setError(null);
        const data = await api.presentations.listSlides(presentationId);
        setSlides(data);
        if (data.length > 0) {
          setSelectedSlideId(data[0].id);
          const firstDraft = makeDraft(data[0]);
          setDraft(firstDraft);
          setDraftsBySlideId({ [data[0].id]: firstDraft });
          setSelectedTextBlockId(firstDraft.textBlocks[0]?.id ?? null);
        }
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
        const [settings, status] = await Promise.all([
          api.videoSettings.get(params.projectId),
          api.generation.status(params.projectId),
        ]);
        setVideoSettings(settings);
        setElevenlabsVoiceId(settings.elevenlabs_voice_id || "");
        setGenerationStatus(status);
      } catch (err) {
        setVideoError(
          err instanceof Error ? err.message : "No se pudo cargar la configuracion de video.",
        );
      }
    }

    void loadVideoExportState();
  }, [params.projectId]);

  useEffect(() => {
    const activeStatuses: GenerationStatus["status"][] = [
      "queued",
      "validating",
      "generating_audio",
      "generating_avatar",
      "rendering_slides",
      "composing_video",
    ];
    if (!activeStatuses.includes(generationStatus.status)) return;

    const timer = window.setInterval(async () => {
      try {
        setGenerationStatus(await api.generation.status(params.projectId));
      } catch (err) {
        setVideoError(err instanceof Error ? err.message : "No se pudo actualizar el progreso.");
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [generationStatus.status, params.projectId]);

  useEffect(() => {
    return () => {
      if (avatarPreviewUrl) URL.revokeObjectURL(avatarPreviewUrl);
    };
  }, [avatarPreviewUrl]);

  function markDirty() {
    setSaveState("idle");
    setIsDirty(true);
    setSaveError(null);
  }

  function updateDraft(update: Partial<SlideDraft>) {
    setDraft((current) => {
      const next = { ...current, ...update };
      if (selectedSlideId) {
        setDraftsBySlideId((drafts) => ({
          ...drafts,
          [selectedSlideId]: next,
        }));
      }
      return next;
    });
    markDirty();
  }

  function updateAvatar(update: Partial<AvatarLayout>) {
    updateDraft({
      avatar: normalizeAvatar(
        { ...draft.avatar, ...update },
        draft.canvasWidth,
        draft.canvasHeight,
      ),
    });
  }

  function updateTextBlock(blockId: string, text: string) {
    const textBlocks = draft.textBlocks.map((block) =>
      block.id === blockId
        ? normalizeTextBlock({ ...block, text }, draft.canvasWidth, draft.canvasHeight)
        : block,
    );
    const title = textBlocks.find((block) => block.type === "title")?.text ?? draft.title;
    const visibleText = textBlocks.map((block) => block.text).join("\n\n");
    updateDraft({ title, visibleText, textBlocks });
  }

  function updateTextBlockLayout(blockId: string, update: Partial<TextBlockLayout>) {
    const textBlocks = draft.textBlocks.map((block) =>
      block.id === blockId
        ? normalizeTextBlock(
            { ...block, ...update },
            draft.canvasWidth,
            draft.canvasHeight,
          )
        : block,
    );
    updateDraft({ textBlocks });
  }

  function getCanvasPoint(event: ReactPointerEvent<HTMLElement>) {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: clamp(
        (event.clientX - rect.left) * (draft.canvasWidth / rect.width),
        0,
        draft.canvasWidth,
      ),
      y: clamp(
        (event.clientY - rect.top) * (draft.canvasHeight / rect.height),
        0,
        draft.canvasHeight,
      ),
    };
  }

  function selectSlide(slide: Slide) {
    setSelectedSlideId(slide.id);
    const nextDraft = draftsBySlideId[slide.id] || makeDraft(slide);
    setDraft(nextDraft);
    setSelectedTextBlockId(nextDraft.textBlocks[0]?.id ?? null);
    setEditingTextBlockId(null);
    setSaveState("idle");
    setSaveError(null);
  }

  function handleAvatarPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (!draft.avatar.enabled) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = getCanvasPoint(event);
    setDragOffset({
      x: point.x - draft.avatar.x,
      y: point.y - draft.avatar.y,
    });
  }

  function handleTextPointerDown(
    event: ReactPointerEvent<HTMLDivElement>,
    block: TextBlockLayout,
  ) {
    if (editingTextBlockId === block.id) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    event.stopPropagation();
    const point = getCanvasPoint(event);
    setSelectedTextBlockId(block.id);
    setTextDrag({
      blockId: block.id,
      offsetX: point.x - block.x,
      offsetY: point.y - block.y,
    });
  }

  function handleCanvasPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const point = getCanvasPoint(event);
    if (dragOffset) {
      updateAvatar({
        x: point.x - dragOffset.x,
        y: point.y - dragOffset.y,
      });
    }
    if (textDrag) {
      updateTextBlockLayout(textDrag.blockId, {
        x: point.x - textDrag.offsetX,
        y: point.y - textDrag.offsetY,
      });
    }
  }

  function handleCanvasPointerUp() {
    setDragOffset(null);
    setTextDrag(null);
  }

  function handleAvatarFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    if (avatarPreviewUrl) URL.revokeObjectURL(avatarPreviewUrl);
    setAvatarPreviewUrl(URL.createObjectURL(file));
  }

  function resetAvatarPosition() {
    updateAvatar(DEFAULT_AVATAR);
  }

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedSlide) return;

    setSaveState("saving");
    setSaveError(null);
    try {
      const updated = await api.slides.update(selectedSlide.id, {
        title: draft.title.trim() || null,
        notes: draft.dialogue,
        dialogue: draft.dialogue,
        visible_text: draft.visibleText,
        metadata: makeMetadata(selectedSlide, draft),
      });
      setSlides((current) =>
        current.map((slide) => (slide.id === updated.id ? updated : slide)),
      );
      const updatedDraft = makeDraft(updated);
      setDraft(updatedDraft);
      setDraftsBySlideId((drafts) => ({
        ...drafts,
        [updated.id]: updatedDraft,
      }));
      setSelectedTextBlockId(updatedDraft.textBlocks[0]?.id ?? null);
      setEditingTextBlockId(null);
      setSaveState("saved");
      setIsDirty(false);
    } catch (err) {
      setSaveState("error");
      setSaveError(err instanceof Error ? err.message : "No se pudo guardar el slide.");
    }
  }

  async function applyAvatarToAllSlides() {
    if (!selectedSlide) return;

    setSaveState("saving");
    setSaveError(null);
    try {
      const updatedSlides = await Promise.all(
        slides.map((slide) =>
          api.slides.update(slide.id, {
            metadata: applyAvatarToMetadata(slide, draft.avatar),
          }),
        ),
      );
      setSlides(updatedSlides);
      const updatedSelected = updatedSlides.find((slide) => slide.id === selectedSlide.id);
      if (updatedSelected) setDraft(makeDraft(updatedSelected));
      setSaveState("saved");
      setIsDirty(false);
    } catch (err) {
      setSaveState("error");
      setSaveError(
        err instanceof Error
          ? err.message
          : "No se pudo aplicar la posicion del avatar a todos los slides.",
      );
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
      setVideoError(err instanceof Error ? err.message : "No se pudo iniciar la generacion.");
    } finally {
      setIsStartingGeneration(false);
    }
  }

  return (
    <AppShell title="Editor">
      <div className="mb-6 flex flex-col gap-2">
        <Link
          href={`/projects/${params.projectId}`}
          className="text-sm font-medium text-brand-700 hover:text-brand-800"
        >
          Volver al proyecto
        </Link>
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Editor Canvas</h2>
          <p className="mt-1 text-sm text-gray-500">
            Revisa la vista del slide, ajusta texto/dialogo y posiciona el avatar antes de generar video.
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
        <form
          onSubmit={handleSave}
          className="grid min-h-[720px] gap-5 xl:grid-cols-[260px_minmax(0,1fr)_340px]"
        >
          <aside className="rounded-lg border border-gray-200 bg-white shadow-sm">
            <div className="border-b border-gray-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-gray-900">Slides</h3>
            </div>
            <div className="max-h-[700px] overflow-auto">
              {slides.map((slide) => {
                const isSelected = slide.id === selectedSlideId;
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
                      {isSelected ? (
                        <span className="rounded-full bg-brand-100 px-2 py-0.5 text-xs font-medium text-brand-700">
                          Activo
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-1 text-sm font-semibold text-gray-900">
                      {slide.title || "Sin titulo"}
                    </p>
                    <p className="mt-1 text-xs text-gray-500">
                      {preview(slide.dialogue || slide.visible_text)}
                    </p>
                  </button>
                );
              })}
            </div>
          </aside>

          {selectedSlide ? (
            <>
              <section className="min-w-0 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium uppercase text-gray-500">
                      Slide {selectedSlide.position}
                    </p>
                    <h3 className="text-sm font-semibold text-gray-900">Canvas</h3>
                  </div>
                  <button
                    type="button"
                    className="rounded-md border border-gray-300 px-3 py-2 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50"
                  >
                    Ready for video generation
                  </button>
                </div>

                <div className="overflow-auto rounded-lg border border-gray-200 bg-gray-100 p-3">
                  <div
                    ref={canvasRef}
                    onPointerMove={handleCanvasPointerMove}
                    onPointerUp={handleCanvasPointerUp}
                    onPointerLeave={handleCanvasPointerUp}
                    className="relative mx-auto w-full min-w-[720px] overflow-hidden rounded-lg bg-slate-950 shadow-inner"
                    style={{ aspectRatio: `${draft.canvasWidth} / ${draft.canvasHeight}` }}
                  >
                    {selectedSlideBackgroundUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={selectedSlideBackgroundUrl}
                        alt={`Slide ${selectedSlide.position} background`}
                        className="absolute inset-0 h-full w-full object-contain"
                      />
                    ) : (
                      <div className="absolute inset-0 bg-slate-100">
                        <div className="absolute inset-[6.67%] rounded-lg border-2 border-dashed border-gray-300 bg-white shadow-sm" />
                        <div className="absolute left-[7.5%] top-[12%] max-w-[84%] rounded-md bg-white/90 px-4 py-3 text-3xl font-bold leading-tight text-gray-900">
                          {draft.title || "Sin titulo"}
                        </div>
                        <div className="absolute left-[7.5%] top-[30%] h-[52%] w-[58%] overflow-hidden whitespace-pre-line rounded-md bg-white/85 px-4 py-3 text-lg leading-relaxed text-gray-700">
                          {draft.visibleText || "Sin texto visible"}
                        </div>
                        <div className="absolute bottom-4 left-4 rounded-md bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800 shadow-sm">
                          Fondo editable del PPT no disponible todavia. Se muestra un canvas basico.
                        </div>
                      </div>
                    )}

                    <div className="absolute inset-0">
                      {draft.textBlocks.map((block) => {
                        const isSelected = block.id === selectedTextBlockId;
                        const isEditing = block.id === editingTextBlockId;
                        const blockStyle = {
                          left: toPercent(block.x, draft.canvasWidth),
                          top: toPercent(block.y, draft.canvasHeight),
                          width: toPercent(block.width, draft.canvasWidth),
                          height: toPercent(block.height, draft.canvasHeight),
                        };

                        const css = textBlockCss(block);
                        if (isEditing) {
                          return (
                            <textarea
                              key={block.id}
                              autoFocus
                              aria-label={
                                block.type === "title"
                                  ? "Editar titulo del slide"
                                  : "Editar texto del slide"
                              }
                              value={block.text}
                              onChange={(event) => updateTextBlock(block.id, event.target.value)}
                              onBlur={() => setEditingTextBlockId(null)}
                              onPointerDown={(event) => event.stopPropagation()}
                              className="absolute z-20 resize-none rounded-md border border-sky-500 bg-transparent px-2 py-1 leading-tight shadow-lg outline-none ring-2 ring-sky-200"
                              style={{
                                ...blockStyle,
                                ...css,
                                zIndex: block.zIndex + 20,
                              }}
                            />
                          );
                        }

                        return (
                          <div
                            key={block.id}
                            role="button"
                            tabIndex={0}
                            aria-label={
                              block.type === "title"
                                ? "Editar titulo del slide"
                                : "Editar texto del slide"
                            }
                            onPointerDown={(event) => handleTextPointerDown(event, block)}
                            onClick={() => {
                              setSelectedTextBlockId(block.id);
                              setEditingTextBlockId(block.id);
                            }}
                            className={`absolute cursor-move touch-none select-none overflow-hidden whitespace-pre-wrap rounded-sm border px-1 py-0.5 transition-colors ${
                              isSelected
                                ? "border-sky-500 bg-white/10 ring-2 ring-sky-200"
                                : "border-transparent hover:border-sky-400"
                            }`}
                            style={{
                              ...blockStyle,
                              ...css,
                              zIndex: block.zIndex,
                            }}
                          >
                            {block.text}
                          </div>
                        );
                      })}
                    </div>

                    {draft.avatar.enabled ? (
                      <div
                        role="button"
                        tabIndex={0}
                        onPointerDown={handleAvatarPointerDown}
                        className="absolute flex cursor-move touch-none select-none items-center justify-center overflow-hidden rounded-2xl border-4 border-sky-600 bg-sky-100 text-xl font-bold text-sky-700 shadow-lg"
                        style={{
                          left: toPercent(draft.avatar.x, draft.canvasWidth),
                          top: toPercent(draft.avatar.y, draft.canvasHeight),
                          width: toPercent(draft.avatar.width, draft.canvasWidth),
                          height: toPercent(draft.avatar.height, draft.canvasHeight),
                          backgroundImage: avatarPreviewUrl ? `url(${avatarPreviewUrl})` : undefined,
                          backgroundPosition: "center",
                          backgroundSize: "cover",
                        }}
                      >
                        {avatarPreviewUrl ? null : "Avatar"}
                      </div>
                    ) : null}
                  </div>
                </div>
              </section>

              <aside className="rounded-lg border border-gray-200 bg-white shadow-sm">
                <div className="border-b border-gray-200 px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold text-gray-900">Propiedades</h3>
                    <div className="flex items-center gap-2">
                      {saveState === "saved" ? (
                        <span className="text-xs font-medium text-green-700">Guardado</span>
                      ) : null}
                      {saveState === "error" ? (
                        <span className="text-xs font-medium text-red-700">Error</span>
                      ) : null}
                      <button
                        type="submit"
                        disabled={saveState === "saving"}
                        className="rounded-md bg-brand-600 px-3 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-400"
                      >
                        {saveState === "saving" ? "Guardando..." : "Guardar"}
                      </button>
                    </div>
                  </div>
                </div>

                <div className="max-h-[700px] space-y-5 overflow-auto p-4">
                  {saveError ? (
                    <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                      {saveError}
                    </div>
                  ) : null}

                  {!draft.dialogue ? (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                      El dialogo esta vacio. La generacion de video necesitara dialogo por slide.
                    </div>
                  ) : null}

                  <section className="space-y-3">
                    <h4 className="text-sm font-semibold text-gray-900">Text</h4>
                    <p className="text-xs text-gray-500">
                      Selecciona un bloque en el Canvas o en esta lista. Al guardar, el PPTX se
                      actualiza y la vista renderizada se regenera.
                    </p>
                    <div className="space-y-2">
                      {draft.textBlocks.map((block, index) => {
                        const isSelected = block.id === selectedTextBlockId;
                        return (
                          <button
                            key={block.id}
                            type="button"
                            onClick={() => {
                              setSelectedTextBlockId(block.id);
                              setEditingTextBlockId(block.id);
                            }}
                            className={`w-full rounded-md border px-3 py-2 text-left text-sm transition-colors ${
                              isSelected
                                ? "border-sky-400 bg-sky-50 text-sky-900"
                                : "border-gray-200 text-gray-700 hover:bg-gray-50"
                            }`}
                          >
                            <span className="block text-xs font-medium uppercase text-gray-500">
                              {block.type === "title" ? "Titulo" : `Texto ${index + 1}`}
                            </span>
                            <span className="mt-1 block truncate">
                              {block.text || "Sin texto"}
                            </span>
                          </button>
                        );
                      })}
                    </div>

                    {draft.textBlocks
                      .filter((block) => block.id === selectedTextBlockId)
                      .map((block) => (
                        <div key={block.id} className="block">
                          <span className="text-sm font-medium text-gray-700">
                            Editar {block.type === "title" ? "titulo" : "texto"}
                          </span>
                          <textarea
                            value={block.text}
                            onChange={(event) => updateTextBlock(block.id, event.target.value)}
                            onFocus={() => setEditingTextBlockId(block.id)}
                            className="mt-1 min-h-32 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                          />
                          <div className="mt-3 grid grid-cols-2 gap-3">
                            {[
                              ["X", "x"],
                              ["Y", "y"],
                              ["Ancho", "width"],
                              ["Alto", "height"],
                            ].map(([label, key]) => (
                              <label key={key} className="block">
                                <span className="text-xs font-medium text-gray-600">
                                  {label}
                                </span>
                                <input
                                  type="number"
                                  value={Math.round(Number(block[key as keyof TextBlockLayout]))}
                                  onChange={(event) =>
                                    updateTextBlockLayout(block.id, {
                                      [key]: Number(event.target.value),
                                    } as Partial<TextBlockLayout>)
                                  }
                                  className="mt-1 w-full rounded-md border border-gray-300 px-2 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                                />
                              </label>
                            ))}
                          </div>
                          <p className="mt-2 text-xs text-gray-500">
                            Fuente original: {block.originalFontFamily || "No especificada"}.
                            Fallback: {block.fallbackFontFamily}.
                          </p>
                        </div>
                      ))}
                  </section>

                  <section className="space-y-3">
                    <h4 className="text-sm font-semibold text-gray-900">Avatar</h4>
                    <label className="flex items-center gap-2 text-sm text-gray-700">
                      <input
                        type="checkbox"
                        checked={draft.avatar.enabled}
                        onChange={(event) => updateAvatar({ enabled: event.target.checked })}
                        className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                      />
                      Avatar habilitado
                    </label>

                    <label className="block">
                      <span className="text-sm font-medium text-gray-700">Imagen local</span>
                      <input
                        type="file"
                        accept="image/*"
                        onChange={handleAvatarFile}
                        className="mt-1 w-full text-sm text-gray-700 file:mr-3 file:rounded-md file:border-0 file:bg-gray-100 file:px-3 file:py-2 file:text-sm file:font-semibold file:text-gray-700"
                      />
                      <p className="mt-1 text-xs text-gray-500">
                        Vista previa local. La imagen no se persiste todavia.
                      </p>
                    </label>

                    <div className="grid grid-cols-2 gap-3">
                      <label className="block">
                        <span className="text-xs font-medium text-gray-600">X</span>
                        <input
                          type="number"
                          value={Math.round(draft.avatar.x)}
                          onChange={(event) => updateAvatar({ x: Number(event.target.value) })}
                          className="mt-1 w-full rounded-md border border-gray-300 px-2 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                        />
                      </label>
                      <label className="block">
                        <span className="text-xs font-medium text-gray-600">Y</span>
                        <input
                          type="number"
                          value={Math.round(draft.avatar.y)}
                          onChange={(event) => updateAvatar({ y: Number(event.target.value) })}
                          className="mt-1 w-full rounded-md border border-gray-300 px-2 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                        />
                      </label>
                      <label className="block">
                        <span className="text-xs font-medium text-gray-600">Ancho</span>
                        <input
                          type="number"
                          value={Math.round(draft.avatar.width)}
                          onChange={(event) => updateAvatar({ width: Number(event.target.value) })}
                          className="mt-1 w-full rounded-md border border-gray-300 px-2 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                        />
                      </label>
                      <label className="block">
                        <span className="text-xs font-medium text-gray-600">Alto</span>
                        <input
                          type="number"
                          value={Math.round(draft.avatar.height)}
                          onChange={(event) => updateAvatar({ height: Number(event.target.value) })}
                          className="mt-1 w-full rounded-md border border-gray-300 px-2 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                        />
                      </label>
                    </div>

                    <div className="flex flex-col gap-2">
                      <button
                        type="button"
                        onClick={resetAvatarPosition}
                        className="rounded-md border border-gray-300 px-3 py-2 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50"
                      >
                        Reset avatar position
                      </button>
                      <button
                        type="button"
                        onClick={applyAvatarToAllSlides}
                        disabled={saveState === "saving"}
                        className="rounded-md border border-brand-200 px-3 py-2 text-sm font-semibold text-brand-700 transition-colors hover:bg-brand-50 disabled:cursor-not-allowed disabled:text-gray-400"
                      >
                        Apply avatar position to all slides
                      </button>
                    </div>
                  </section>

                  <section className="space-y-3">
                    <h4 className="text-sm font-semibold text-gray-900">Dialogue</h4>
                    <textarea
                      value={draft.dialogue}
                      onChange={(event) => updateDraft({ dialogue: event.target.value })}
                      placeholder="Escribe el dialogo para narracion"
                      className="min-h-36 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                    />
                  </section>

                  <section className="space-y-4 rounded-lg border border-gray-200 bg-gray-50 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <h4 className="text-sm font-semibold text-gray-900">Video Export</h4>
                        <p className="mt-1 text-xs text-gray-500">
                          Configure ElevenLabs and WaveSpeed, then generate from the saved editor state.
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

                    <div className="space-y-3 rounded-md border border-gray-200 bg-white p-3">
                      <h5 className="text-xs font-semibold uppercase text-gray-500">
                        Required API Credentials
                      </h5>
                      <label className="block">
                        <span className="text-xs font-medium text-gray-600">
                          ElevenLabs API Key
                        </span>
                        <input
                          type="password"
                          value={elevenlabsApiKey}
                          onChange={(event) => setElevenlabsApiKey(event.target.value)}
                          placeholder={
                            videoSettings?.elevenlabs_api_key_masked || "Paste API key"
                          }
                          className="mt-1 w-full rounded-md border border-gray-300 px-2 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                        />
                        {videoSettings?.elevenlabs_api_key_masked ? (
                          <span className="mt-1 block text-xs text-gray-500">
                            Saved: {videoSettings.elevenlabs_api_key_masked}
                          </span>
                        ) : null}
                      </label>

                      <label className="block">
                        <span className="text-xs font-medium text-gray-600">
                          ElevenLabs Voice ID
                        </span>
                        <input
                          type="text"
                          value={elevenlabsVoiceId}
                          onChange={(event) => setElevenlabsVoiceId(event.target.value)}
                          className="mt-1 w-full rounded-md border border-gray-300 px-2 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                        />
                      </label>

                      <label className="block">
                        <span className="text-xs font-medium text-gray-600">
                          WaveSpeed API Key
                        </span>
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
                    </div>

                    {!allSlidesHaveDialogue ? (
                      <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                        Every slide needs dialogue before video generation.
                      </div>
                    ) : null}
                    {isDirty ? (
                      <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                        Save slide edits before generating video.
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

                    <div className="space-y-2 rounded-md border border-gray-200 bg-white p-3">
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
                      {generationStatus.status === "failed" ? (
                        <p className="text-xs text-red-700">
                          {generationStatus.error_message || generationStatus.error_code}
                        </p>
                      ) : null}
                    </div>

                    <button
                      type="button"
                      onClick={startVideoGeneration}
                      disabled={!canGenerateVideo || isStartingGeneration}
                      className="w-full rounded-md bg-brand-600 px-3 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-400"
                    >
                      {isStartingGeneration ? "Starting..." : "Generate Video"}
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
                  </section>
                </div>
              </aside>
            </>
          ) : null}
        </form>
      )}
    </AppShell>
  );
}
