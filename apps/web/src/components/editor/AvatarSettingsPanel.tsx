"use client";

import { ChangeEvent } from "react";
import {
  AvatarFitMode,
  CANVAS_H,
  CANVAS_W,
  ProjectAvatar,
  SlideAvatarMeta,
} from "@/lib/api";

interface AvatarSettingsPanelProps {
  /* project-level avatar */
  projectAvatar: ProjectAvatar | null;
  avatarPreviewUrl: string | null;
  avatarUploadState: "idle" | "uploading" | "saved" | "error";
  avatarUploadError: string | null;
  onAvatarFileChange: (e: ChangeEvent<HTMLInputElement>) => void;

  /* per-slide avatar meta (null when no slide is selected) */
  slideMeta: SlideAvatarMeta | null;
  onSlideMetaChange: (updated: SlideAvatarMeta) => void;
  onSaveSlideAvatarMeta: () => Promise<void>;
  isSavingSlideAvatarMeta: boolean;

  /* bulk actions */
  onApplyToAllSlides: () => Promise<void>;
  isApplyingToAll: boolean;
  onResetToProjectDefault: () => void;

  hasSlide: boolean;

  /**
   * "card" (default) renders the panel as a standalone card with its own header.
   * "bare" drops the outer border/shadow and internal header so the panel can be
   * embedded inside an accordion/collapsible section that already provides them.
   */
  variant?: "card" | "bare";
}

const FIT_MODES: { value: AvatarFitMode; label: string; description: string }[] = [
  { value: "custom", label: "Personalizado", description: "Posición libre" },
  { value: "full_slide", label: "Pantalla completa", description: "Cubrir toda la lámina" },
  { value: "cover", label: "Cubrir", description: "Rellenar, recortando bordes" },
  { value: "contain", label: "Contener", description: "Ajustar dentro, con margen" },
];

export function AvatarSettingsPanel({
  projectAvatar,
  avatarPreviewUrl,
  avatarUploadState,
  avatarUploadError,
  onAvatarFileChange,
  slideMeta,
  onSlideMetaChange,
  onSaveSlideAvatarMeta,
  isSavingSlideAvatarMeta,
  onApplyToAllSlides,
  isApplyingToAll,
  onResetToProjectDefault,
  hasSlide,
  variant = "card",
}: AvatarSettingsPanelProps) {
  const isBare = variant === "bare";
  function update(patch: Partial<SlideAvatarMeta>) {
    if (!slideMeta) return;
    onSlideMetaChange({ ...slideMeta, ...patch });
  }

  function applyFitMode(mode: AvatarFitMode) {
    if (!slideMeta) return;
    if (mode === "full_slide") {
      onSlideMetaChange({ ...slideMeta, fitMode: mode, x: 0, y: 0, width: CANVAS_W, height: CANVAS_H });
    } else if (mode === "cover") {
      // Center a 16:9 block that is 80% of the canvas height
      const h = Math.round(CANVAS_H * 0.8);
      const w = Math.round((h * 16) / 9);
      onSlideMetaChange({
        ...slideMeta,
        fitMode: mode,
        x: Math.round((CANVAS_W - w) / 2),
        y: Math.round((CANVAS_H - h) / 2),
        width: w,
        height: h,
      });
    } else if (mode === "contain") {
      // Smaller centered block — 50% height
      const h = Math.round(CANVAS_H * 0.5);
      const w = Math.round(h * 0.75); // 3:4 portrait
      onSlideMetaChange({
        ...slideMeta,
        fitMode: mode,
        x: Math.round((CANVAS_W - w) / 2),
        y: Math.round((CANVAS_H - h) / 2),
        width: w,
        height: h,
      });
    } else {
      onSlideMetaChange({ ...slideMeta, fitMode: mode });
    }
  }

  const inputCls =
    "w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100";

  return (
    <section
      className={
        isBare
          ? "space-y-5 p-4"
          : "space-y-5 rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
      }
    >
      {/* ── Header (hidden in bare mode; the accordion provides the title) ── */}
      {!isBare && (
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Configuraciones del avatar</h3>
          <p className="mt-0.5 text-xs text-gray-500">
            Sube una imagen de avatar y configura su ubicación en cada lámina.
          </p>
        </div>
      )}

      {/* ── Upload ── */}
      <div>
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Imagen del avatar
        </span>
        <label className="mt-2 block">
          <span className="sr-only">Subir avatar</span>
          <input
            type="file"
            accept="image/*"
            onChange={onAvatarFileChange}
            disabled={avatarUploadState === "uploading"}
            className="w-full text-sm text-gray-700 file:mr-3 file:rounded-md file:border-0 file:bg-gray-100 file:px-3 file:py-1.5 file:text-sm file:font-semibold file:text-gray-700 hover:file:bg-gray-200"
          />
        </label>

        <div className="mt-3 flex items-start gap-3">
          {avatarPreviewUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={avatarPreviewUrl}
              alt="Avatar preview"
              className="h-16 w-16 flex-shrink-0 rounded-md border border-gray-200 object-cover"
              style={{ borderRadius: slideMeta ? `${slideMeta.borderRadius}%` : undefined }}
            />
          ) : (
            <div className="flex h-16 w-16 flex-shrink-0 items-center justify-center rounded-md border border-dashed border-gray-300 bg-gray-50">
              <span className="text-xs text-gray-400">Sin imagen</span>
            </div>
          )}
          <div className="min-w-0 text-xs">
            {avatarUploadState === "uploading" && (
              <p className="text-blue-700">Subiendo…</p>
            )}
            {avatarUploadState === "saved" && projectAvatar && (
              <p className="text-green-700">Guardado — {projectAvatar.filename}</p>
            )}
            {avatarUploadState === "error" && (
              <p className="text-red-700">{avatarUploadError || "Error al subir."}</p>
            )}
            {avatarUploadState === "idle" && !projectAvatar && (
              <p className="text-gray-500">Aún no se ha subido ningún avatar.</p>
            )}
          </div>
        </div>
      </div>

      {/* ── Per-slide controls (only shown when a slide is selected) ── */}
      {hasSlide && slideMeta ? (
        <>
          <hr className="border-gray-100" />

          {/* Visibility */}
          <div>
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Visibilidad en esta lámina
            </span>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => update({ visible: true })}
                className={`flex-1 rounded-md border px-3 py-1.5 text-xs font-semibold transition-colors ${
                  slideMeta.visible
                    ? "border-brand-600 bg-brand-600 text-white"
                    : "border-gray-300 text-gray-700 hover:bg-gray-50"
                }`}
              >
                Mostrar avatar en esta lámina
              </button>
              <button
                type="button"
                onClick={() => update({ visible: false })}
                className={`flex-1 rounded-md border px-3 py-1.5 text-xs font-semibold transition-colors ${
                  !slideMeta.visible
                    ? "border-red-500 bg-red-500 text-white"
                    : "border-gray-300 text-gray-700 hover:bg-gray-50"
                }`}
              >
                Ocultar avatar en esta lámina
              </button>
            </div>
          </div>

          {/* Fit mode */}
          <div>
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Ajuste del avatar
            </span>
            <div className="mt-2 grid grid-cols-2 gap-1.5">
              {FIT_MODES.map((mode) => (
                <button
                  key={mode.value}
                  type="button"
                  onClick={() => applyFitMode(mode.value)}
                  title={mode.description}
                  className={`rounded-md border px-2 py-1.5 text-xs font-semibold transition-colors ${
                    slideMeta.fitMode === mode.value
                      ? "border-brand-600 bg-brand-50 text-brand-700"
                      : "border-gray-200 text-gray-700 hover:bg-gray-50"
                  }`}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          </div>

          {/* Shape */}
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                Forma del avatar
              </span>
              <span className="text-xs text-gray-500">
                {slideMeta.borderRadius === 0
                  ? "Cuadrado"
                  : slideMeta.borderRadius >= 50
                    ? "Redondo"
                    : `Bordes redondeados (${slideMeta.borderRadius}%)`}
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={50}
              step={1}
              value={slideMeta.borderRadius}
              onChange={(e) => update({ borderRadius: Number(e.target.value) })}
              className="mt-2 w-full accent-brand-600"
            />
            <div className="mt-1 flex justify-between text-xs text-gray-400">
              <span>Cuadrado</span>
              <span>Redondo</span>
            </div>
          </div>

          {/* Position */}
          <div>
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Tamaño y posición
            </span>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <label className="block">
                <span className="text-xs text-gray-600">Posición X</span>
                <input
                  type="number"
                  value={Math.round(slideMeta.x)}
                  min={0}
                  max={CANVAS_W}
                  onChange={(e) => update({ x: Number(e.target.value) })}
                  className={inputCls}
                />
              </label>
              <label className="block">
                <span className="text-xs text-gray-600">Posición Y</span>
                <input
                  type="number"
                  value={Math.round(slideMeta.y)}
                  min={0}
                  max={CANVAS_H}
                  onChange={(e) => update({ y: Number(e.target.value) })}
                  className={inputCls}
                />
              </label>
              <label className="block">
                <span className="text-xs text-gray-600">Ancho</span>
                <input
                  type="number"
                  value={Math.round(slideMeta.width)}
                  min={20}
                  max={CANVAS_W}
                  onChange={(e) => update({ width: Number(e.target.value) })}
                  className={inputCls}
                />
              </label>
              <label className="block">
                <span className="text-xs text-gray-600">Alto</span>
                <input
                  type="number"
                  value={Math.round(slideMeta.height)}
                  min={20}
                  max={CANVAS_H}
                  onChange={(e) => update({ height: Number(e.target.value) })}
                  className={inputCls}
                />
              </label>
            </div>
          </div>

          {/* Save */}
          <button
            type="button"
            onClick={onSaveSlideAvatarMeta}
            disabled={isSavingSlideAvatarMeta}
            className="w-full rounded-md bg-brand-600 px-3 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-400"
          >
            {isSavingSlideAvatarMeta ? "Guardando…" : "Guardar cambios"}
          </button>

          {/* Bulk / reset actions */}
          <div className="space-y-2">
            <button
              type="button"
              onClick={onApplyToAllSlides}
              disabled={isApplyingToAll}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-xs font-semibold text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-400"
            >
              {isApplyingToAll ? "Aplicando…" : "Aplicar posición a todas las láminas"}
            </button>
            <button
              type="button"
              onClick={onResetToProjectDefault}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-500 transition-colors hover:bg-gray-50"
            >
              Restablecer configuración
            </button>
          </div>
        </>
      ) : (
        !hasSlide && (
          <p className="text-xs text-gray-400">
            Selecciona una lámina para configurar la ubicación del avatar.
          </p>
        )
      )}
    </section>
  );
}
