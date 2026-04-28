"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Slide, api } from "@/lib/api";

type SaveState = "idle" | "saving" | "saved" | "error";

interface SlideDraft {
  title: string;
  notes: string;
  dialogue: string;
}

function preview(value: string | null | undefined) {
  if (!value) return "Sin contenido";
  return value.length > 90 ? `${value.slice(0, 90)}...` : value;
}

function makeDraft(slide: Slide): SlideDraft {
  return {
    title: slide.title || "",
    notes: slide.notes || "",
    dialogue: slide.dialogue || "",
  };
}

export default function ProjectEditorPage() {
  const params = useParams<{ projectId: string }>();
  const searchParams = useSearchParams();
  const presentationId = searchParams.get("presentationId");
  const [slides, setSlides] = useState<Slide[]>([]);
  const [selectedSlideId, setSelectedSlideId] = useState<string | null>(null);
  const [draft, setDraft] = useState<SlideDraft>({ title: "", notes: "", dialogue: "" });
  const [isLoading, setIsLoading] = useState(Boolean(presentationId));
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);

  const selectedSlide = useMemo(
    () => slides.find((slide) => slide.id === selectedSlideId) ?? null,
    [selectedSlideId, slides],
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
          setDraft(makeDraft(data[0]));
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudieron cargar los slides.");
      } finally {
        setIsLoading(false);
      }
    }

    void loadSlides();
  }, [presentationId]);

  function selectSlide(slide: Slide) {
    setSelectedSlideId(slide.id);
    setDraft(makeDraft(slide));
    setSaveState("idle");
    setSaveError(null);
  }

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedSlide) return;

    setSaveState("saving");
    setSaveError(null);
    try {
      const updated = await api.slides.update(selectedSlide.id, {
        title: draft.title.trim() || null,
        notes: draft.notes,
        dialogue: draft.dialogue,
      });
      setSlides((current) =>
        current.map((slide) => (slide.id === updated.id ? updated : slide)),
      );
      setDraft(makeDraft(updated));
      setSaveState("saved");
    } catch (err) {
      setSaveState("error");
      setSaveError(err instanceof Error ? err.message : "No se pudo guardar el slide.");
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
          <h2 className="text-xl font-semibold text-gray-900">Editor de slides</h2>
          <p className="mt-1 text-sm text-gray-500">
            Ajusta titulo, notas y dialogo antes de generar audio o video.
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
        <div className="grid min-h-[620px] gap-6 lg:grid-cols-[280px_1fr]">
          <aside className="rounded-lg border border-gray-200 bg-white shadow-sm">
            <div className="border-b border-gray-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-gray-900">Slides</h3>
            </div>
            <div className="max-h-[680px] overflow-auto">
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
                    <p className="mt-1 text-xs text-gray-500">{preview(slide.dialogue || slide.notes)}</p>
                  </button>
                );
              })}
            </div>
          </aside>

          {selectedSlide ? (
            <form
              onSubmit={handleSave}
              className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
            >
              <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <p className="text-xs font-medium uppercase text-gray-500">
                    Slide {selectedSlide.position}
                  </p>
                  <h3 className="mt-1 text-lg font-semibold text-gray-900">
                    {selectedSlide.title || "Sin titulo"}
                  </h3>
                </div>
                <div className="flex items-center gap-3">
                  {saveState === "saved" ? (
                    <span className="text-sm font-medium text-green-700">Guardado</span>
                  ) : null}
                  {saveState === "error" ? (
                    <span className="text-sm font-medium text-red-700">Error</span>
                  ) : null}
                  <button
                    type="submit"
                    disabled={saveState === "saving"}
                    className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-400"
                  >
                    {saveState === "saving" ? "Guardando..." : "Guardar"}
                  </button>
                </div>
              </div>

              {saveError ? (
                <div className="mb-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {saveError}
                </div>
              ) : null}

              <div className="grid gap-5">
                <label className="block">
                  <span className="text-sm font-medium text-gray-700">Titulo</span>
                  <input
                    value={draft.title}
                    onChange={(event) => {
                      setDraft((current) => ({ ...current, title: event.target.value }));
                      setSaveState("idle");
                    }}
                    className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                    maxLength={500}
                  />
                </label>

                <div>
                  <p className="text-sm font-medium text-gray-700">Texto visible</p>
                  <div className="mt-1 max-h-44 overflow-auto rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
                    <p className="whitespace-pre-line">
                      {selectedSlide.visible_text || "Sin texto visible"}
                    </p>
                  </div>
                </div>

                <label className="block">
                  <span className="text-sm font-medium text-gray-700">Notas del expositor</span>
                  <textarea
                    value={draft.notes}
                    onChange={(event) => {
                      setDraft((current) => ({ ...current, notes: event.target.value }));
                      setSaveState("idle");
                    }}
                    className="mt-1 min-h-32 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                  />
                </label>

                <label className="block">
                  <span className="text-sm font-medium text-gray-700">Dialogo</span>
                  {!draft.dialogue && draft.notes ? (
                    <p className="mt-1 text-xs text-gray-500">
                      Sugerencia inicial: usa las notas del expositor como dialogo.
                    </p>
                  ) : null}
                  <textarea
                    value={draft.dialogue}
                    onChange={(event) => {
                      setDraft((current) => ({ ...current, dialogue: event.target.value }));
                      setSaveState("idle");
                    }}
                    placeholder={draft.notes || "Escribe el dialogo para narracion"}
                    className="mt-1 min-h-40 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                  />
                </label>
              </div>
            </form>
          ) : null}
        </div>
      )}
    </AppShell>
  );
}
