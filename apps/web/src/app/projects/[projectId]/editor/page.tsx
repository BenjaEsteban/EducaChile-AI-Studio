"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Slide, api } from "@/lib/api";

function preview(value: string | null | undefined) {
  if (!value) return "Sin contenido";
  return value.length > 180 ? `${value.slice(0, 180)}...` : value;
}

export default function ProjectEditorPage() {
  const params = useParams<{ projectId: string }>();
  const searchParams = useSearchParams();
  const presentationId = searchParams.get("presentationId");
  const [slides, setSlides] = useState<Slide[]>([]);
  const [isLoading, setIsLoading] = useState(Boolean(presentationId));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadSlides() {
      if (!presentationId) return;

      try {
        setError(null);
        const data = await api.presentations.listSlides(presentationId);
        setSlides(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudieron cargar los slides.");
      } finally {
        setIsLoading(false);
      }
    }

    void loadSlides();
  }, [presentationId]);

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
          <h2 className="text-xl font-semibold text-gray-900">Slides parseados</h2>
          <p className="mt-1 text-sm text-gray-500">
            Revisa el contenido extraido de la presentacion.
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
        <div className="space-y-4">
          {slides.map((slide) => (
            <article key={slide.id} className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <p className="text-xs font-medium uppercase text-gray-500">
                    Slide {slide.position}
                  </p>
                  <h3 className="mt-1 text-base font-semibold text-gray-900">
                    {slide.title || "Sin titulo"}
                  </h3>
                </div>
                <span className="text-xs text-gray-500">{slide.id}</span>
              </div>

              <div className="mt-4 grid gap-4 md:grid-cols-2">
                <div>
                  <p className="text-xs font-medium uppercase text-gray-500">Notas</p>
                  <p className="mt-1 whitespace-pre-line text-sm text-gray-700">
                    {preview(slide.notes)}
                  </p>
                </div>
                <div>
                  <p className="text-xs font-medium uppercase text-gray-500">Dialogo</p>
                  <p className="mt-1 whitespace-pre-line text-sm text-gray-700">
                    {preview(slide.dialogue)}
                  </p>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </AppShell>
  );
}
