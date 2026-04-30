"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  ChangeEvent,
  FormEvent,
  PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Slide, api } from "@/lib/api";

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
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  fontSize: number;
  fontWeight: "400" | "600" | "700";
  color: string;
  textAlign: "left" | "center" | "right";
}

interface CanvasLayout {
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
  avatar: AvatarLayout;
  textBlocks: TextBlockLayout[];
}

const CANVAS_WIDTH = 960;
const CANVAS_HEIGHT = 540;
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
  fontSize: 34,
  fontWeight: "700",
  color: "#111827",
  textAlign: "left",
};

const DEFAULT_BODY_BLOCK: Omit<TextBlockLayout, "text"> = {
  id: "body-1",
  type: "body",
  x: 72,
  y: 180,
  width: 560,
  height: 260,
  fontSize: 22,
  fontWeight: "400",
  color: "#1f2937",
  textAlign: "left",
};

function preview(value: string | null | undefined) {
  if (!value) return "Sin contenido";
  return value.length > 90 ? `${value.slice(0, 90)}...` : value;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function normalizeAvatar(avatar: AvatarLayout): AvatarLayout {
  const width = clamp(Number(avatar.width) || DEFAULT_AVATAR.width, 80, CANVAS_WIDTH);
  const height = clamp(Number(avatar.height) || DEFAULT_AVATAR.height, 80, CANVAS_HEIGHT);
  return {
    enabled: Boolean(avatar.enabled),
    width,
    height,
    x: clamp(Number(avatar.x) || 0, 0, CANVAS_WIDTH - width),
    y: clamp(Number(avatar.y) || 0, 0, CANVAS_HEIGHT - height),
  };
}

function normalizeTextBlock(block: TextBlockLayout): TextBlockLayout {
  const width = clamp(Number(block.width) || DEFAULT_BODY_BLOCK.width, 80, CANVAS_WIDTH);
  const height = clamp(Number(block.height) || DEFAULT_BODY_BLOCK.height, 48, CANVAS_HEIGHT);
  return {
    id: block.id || `text-${block.type}`,
    type: block.type === "title" ? "title" : "body",
    text: block.text || "",
    width,
    height,
    x: clamp(Number(block.x) || 0, 0, CANVAS_WIDTH - width),
    y: clamp(Number(block.y) || 0, 0, CANVAS_HEIGHT - height),
    fontSize: clamp(Number(block.fontSize) || DEFAULT_BODY_BLOCK.fontSize, 12, 64),
    fontWeight: ["400", "600", "700"].includes(block.fontWeight)
      ? block.fontWeight
      : "400",
    color: block.color || "#111827",
    textAlign: ["left", "center", "right"].includes(block.textAlign)
      ? block.textAlign
      : "left",
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function getMetadataPreviewUrl(slide: Slide): string | null {
  return (
    slide.preview_image_url ||
    getString(slide.metadata.slide_preview_url) ||
    getString(slide.metadata.preview_url) ||
    getString(slide.metadata.thumbnail_url) ||
    getString(slide.metadata.rendered_image_url) ||
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

function getTextBlockFontWeight(block: Record<string, unknown>): TextBlockLayout["fontWeight"] {
  const value = getTextBlockString(block, "fontWeight");
  return value === "700" || value === "600" ? value : "400";
}

function getTextBlockAlign(block: Record<string, unknown>): TextBlockLayout["textAlign"] {
  const value = getTextBlockString(block, "textAlign");
  return value === "center" || value === "right" ? value : "left";
}

function getTextBlocksFromMetadata(
  canvas: Record<string, unknown>,
  title: string,
  visibleText: string,
): TextBlockLayout[] {
  const rawBlocks = Array.isArray(canvas.text_blocks) ? canvas.text_blocks : [];
  const blocks = rawBlocks
    .filter(isRecord)
    .map((block, index) =>
      normalizeTextBlock({
        id: getTextBlockString(block, "id") || `text-${index}`,
        type: getTextBlockString(block, "type") === "title" ? "title" : "body",
        text: getTextBlockString(block, "text") || "",
        x: getTextBlockNumber(block, "x") ?? DEFAULT_BODY_BLOCK.x,
        y: getTextBlockNumber(block, "y") ?? DEFAULT_BODY_BLOCK.y,
        width: getTextBlockNumber(block, "width") ?? DEFAULT_BODY_BLOCK.width,
        height: getTextBlockNumber(block, "height") ?? DEFAULT_BODY_BLOCK.height,
        fontSize: getTextBlockNumber(block, "fontSize") ?? DEFAULT_BODY_BLOCK.fontSize,
        fontWeight: getTextBlockFontWeight(block),
        color: getTextBlockString(block, "color") || DEFAULT_BODY_BLOCK.color,
        textAlign: getTextBlockAlign(block),
      }),
    );

  return blocks.length > 0 ? blocks : getDefaultTextBlocks(title, visibleText);
}

function getCanvasLayout(slide: Slide): CanvasLayout {
  const canvas = isRecord(slide.metadata.canvas) ? slide.metadata.canvas : {};
  const avatar = isRecord(canvas.avatar) ? canvas.avatar : {};
  const text = isRecord(canvas.text) ? canvas.text : {};
  const title = typeof text.title === "string" ? text.title : slide.title || "";
  const visibleText =
    typeof text.visible_text === "string" ? text.visible_text : slide.visible_text || "";

  return {
    avatar: normalizeAvatar({
      enabled: typeof avatar.enabled === "boolean" ? avatar.enabled : DEFAULT_AVATAR.enabled,
      x: typeof avatar.x === "number" ? avatar.x : DEFAULT_AVATAR.x,
      y: typeof avatar.y === "number" ? avatar.y : DEFAULT_AVATAR.y,
      width: typeof avatar.width === "number" ? avatar.width : DEFAULT_AVATAR.width,
      height: typeof avatar.height === "number" ? avatar.height : DEFAULT_AVATAR.height,
    }),
    text: {
      title,
      visible_text: visibleText,
    },
    textBlocks: getTextBlocksFromMetadata(canvas, title, visibleText),
  };
}

function makeDraft(slide: Slide): SlideDraft {
  const canvas = getCanvasLayout(slide);
  return {
    title: canvas.text.title,
    visibleText: canvas.text.visible_text,
    dialogue: slide.dialogue || slide.notes || "",
    avatar: canvas.avatar,
    textBlocks: canvas.textBlocks,
  };
}

function makeMetadata(slide: Slide, draft: SlideDraft): Record<string, unknown> {
  return {
    ...slide.metadata,
    visible_text: draft.visibleText,
    dialogue: draft.dialogue,
    canvas: {
      avatar: normalizeAvatar(draft.avatar),
      text: {
        title: draft.title,
        visible_text: draft.visibleText,
      },
      text_blocks: draft.textBlocks.map(normalizeTextBlock),
    },
  };
}

function applyAvatarToMetadata(slide: Slide, avatar: AvatarLayout): Record<string, unknown> {
  const currentCanvas = getCanvasLayout(slide);
  return {
    ...slide.metadata,
    canvas: {
      avatar: normalizeAvatar(avatar),
      text: currentCanvas.text,
      text_blocks: currentCanvas.textBlocks,
    },
  };
}

function toPercent(value: number, total: number) {
  return `${(value / total) * 100}%`;
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
    avatar: DEFAULT_AVATAR,
    textBlocks: getDefaultTextBlocks("", ""),
  });
  const [draftsBySlideId, setDraftsBySlideId] = useState<Record<string, SlideDraft>>({});
  const [isLoading, setIsLoading] = useState(Boolean(presentationId));
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [avatarPreviewUrl, setAvatarPreviewUrl] = useState<string | null>(null);
  const [slidePreviewUrls, setSlidePreviewUrls] = useState<Record<string, string>>({});
  const [dragOffset, setDragOffset] = useState<{ x: number; y: number } | null>(null);
  const [selectedTextBlockId, setSelectedTextBlockId] = useState<string | null>(null);

  const selectedSlide = useMemo(
    () => slides.find((slide) => slide.id === selectedSlideId) ?? null,
    [selectedSlideId, slides],
  );

  const selectedSlidePreviewUrl = selectedSlide
    ? getMetadataPreviewUrl(selectedSlide) || slidePreviewUrls[selectedSlide.id] || null
    : null;

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
    return () => {
      if (avatarPreviewUrl) URL.revokeObjectURL(avatarPreviewUrl);
    };
  }, [avatarPreviewUrl]);

  useEffect(() => {
    async function loadSlidePreview() {
      if (!selectedSlide?.thumbnail_key || slidePreviewUrls[selectedSlide.id]) return;

      try {
        const result = await api.storage.presignedDownload(selectedSlide.thumbnail_key);
        setSlidePreviewUrls((current) => ({
          ...current,
          [selectedSlide.id]: result.url,
        }));
      } catch {
        // A missing preview should not block text/dialogue editing.
      }
    }

    void loadSlidePreview();
  }, [selectedSlide, slidePreviewUrls]);

  function markDirty() {
    setSaveState("idle");
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
      avatar: normalizeAvatar({ ...draft.avatar, ...update }),
    });
  }

  function updateTextBlock(blockId: string, text: string) {
    const textBlocks = draft.textBlocks.map((block) =>
      block.id === blockId ? normalizeTextBlock({ ...block, text }) : block,
    );
    const title = textBlocks.find((block) => block.type === "title")?.text ?? draft.title;
    const bodyBlocks = textBlocks.filter((block) => block.type !== "title");
    const visibleText = bodyBlocks.map((block) => block.text).join("\n\n");
    updateDraft({ title, visibleText, textBlocks });
  }

  function getCanvasPoint(event: ReactPointerEvent<HTMLElement>) {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: clamp((event.clientX - rect.left) * (CANVAS_WIDTH / rect.width), 0, CANVAS_WIDTH),
      y: clamp((event.clientY - rect.top) * (CANVAS_HEIGHT / rect.height), 0, CANVAS_HEIGHT),
    };
  }

  function selectSlide(slide: Slide) {
    setSelectedSlideId(slide.id);
    const nextDraft = draftsBySlideId[slide.id] || makeDraft(slide);
    setDraft(nextDraft);
    setSelectedTextBlockId(nextDraft.textBlocks[0]?.id ?? null);
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

  function handleCanvasPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (!dragOffset) return;
    const point = getCanvasPoint(event);
    updateAvatar({
      x: point.x - dragOffset.x,
      y: point.y - dragOffset.y,
    });
  }

  function handleCanvasPointerUp() {
    setDragOffset(null);
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
      setSlidePreviewUrls((current) => {
        const next = { ...current };
        delete next[updated.id];
        return next;
      });
      setSelectedTextBlockId(updatedDraft.textBlocks[0]?.id ?? null);
      setSaveState("saved");
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
    } catch (err) {
      setSaveState("error");
      setSaveError(
        err instanceof Error
          ? err.message
          : "No se pudo aplicar la posicion del avatar a todos los slides.",
      );
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
                    className="relative aspect-video w-full min-w-[720px] overflow-hidden rounded-lg bg-slate-950 shadow-inner"
                  >
                    {selectedSlidePreviewUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={selectedSlidePreviewUrl}
                        alt={`Slide ${selectedSlide.position}`}
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
                          Vista renderizada del PPT no disponible todavia. Se muestra el texto extraido.
                        </div>
                      </div>
                    )}

                    <div className="absolute inset-0">
                      {draft.textBlocks.map((block) => {
                        const isSelected = block.id === selectedTextBlockId;
                        return (
                          <button
                            key={block.id}
                            type="button"
                            aria-label={
                              block.type === "title"
                                ? "Seleccionar titulo del slide"
                                : "Seleccionar texto del slide"
                            }
                            onClick={() => setSelectedTextBlockId(block.id)}
                            className={`absolute rounded-md border bg-transparent transition-colors ${
                              isSelected
                                ? "border-sky-500 ring-2 ring-sky-200"
                                : "border-transparent hover:border-sky-400"
                            }`}
                            style={{
                              left: toPercent(block.x, CANVAS_WIDTH),
                              top: toPercent(block.y, CANVAS_HEIGHT),
                              width: toPercent(block.width, CANVAS_WIDTH),
                              height: toPercent(block.height, CANVAS_HEIGHT),
                            }}
                          />
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
                          left: toPercent(draft.avatar.x, CANVAS_WIDTH),
                          top: toPercent(draft.avatar.y, CANVAS_HEIGHT),
                          width: toPercent(draft.avatar.width, CANVAS_WIDTH),
                          height: toPercent(draft.avatar.height, CANVAS_HEIGHT),
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
                            onClick={() => setSelectedTextBlockId(block.id)}
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
                        <label key={block.id} className="block">
                          <span className="text-sm font-medium text-gray-700">
                            Editar {block.type === "title" ? "titulo" : "texto"}
                          </span>
                          <textarea
                            value={block.text}
                            onChange={(event) => updateTextBlock(block.id, event.target.value)}
                            className="mt-1 min-h-32 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                          />
                        </label>
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
                </div>
              </aside>
            </>
          ) : null}
        </form>
      )}
    </AppShell>
  );
}
