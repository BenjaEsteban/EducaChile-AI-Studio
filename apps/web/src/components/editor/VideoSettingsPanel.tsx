"use client";

import { ChangeEvent, useEffect, useState } from "react";

import {
  BackgroundMusicSettings,
  MediaSettings,
  SubtitlePosition,
  SubtitleSettings,
  SUBTITLE_MAX_FONT_SIZE,
  SUBTITLE_MIN_FONT_SIZE,
  VideoSettings,
} from "@/lib/api";

const DEFAULT_MUSIC: BackgroundMusicSettings = {
  enabled: false,
  asset_id: null,
  loop: true,
  volume: 0.35,
  fade_out_enabled: true,
  fade_out_seconds: 3,
};

const DEFAULT_SUBS: SubtitleSettings = {
  enabled: true,
  font_family: "Arial",
  font_size: 24,
  text_color: "#FFFFFF",
  background_color: "#000000",
  background_opacity: 0.5,
  position: "bottom",
};

const FONT_OPTIONS = ["Arial", "Verdana", "Georgia", "Tahoma", "Times New Roman", "Courier New"];
const POSITION_OPTIONS: { value: SubtitlePosition; label: string }[] = [
  { value: "bottom", label: "Inferior" },
  { value: "center", label: "Centro" },
  { value: "top", label: "Superior" },
];

interface VideoSettingsPanelProps {
  videoSettings: VideoSettings | null;
  onSaveMediaSettings: (media: MediaSettings) => Promise<void>;
  isSaving: boolean;
  saveState: "idle" | "saved" | "error";
  saveError: string | null;
  onUploadMusic: (e: ChangeEvent<HTMLInputElement>) => void;
  onDeleteMusic: () => Promise<void>;
  musicState: "idle" | "uploading" | "saved" | "error";
  musicError: string | null;
}

export function VideoSettingsPanel({
  videoSettings,
  onSaveMediaSettings,
  isSaving,
  saveState,
  saveError,
  onUploadMusic,
  onDeleteMusic,
  musicState,
  musicError,
}: VideoSettingsPanelProps) {
  const [music, setMusic] = useState<BackgroundMusicSettings>(DEFAULT_MUSIC);
  const [subs, setSubs] = useState<SubtitleSettings>(DEFAULT_SUBS);

  // Sync local form state from the server settings whenever they change.
  useEffect(() => {
    if (videoSettings?.media_settings) {
      setMusic({ ...DEFAULT_MUSIC, ...videoSettings.media_settings.background_music });
      setSubs({ ...DEFAULT_SUBS, ...videoSettings.media_settings.subtitles });
    }
  }, [videoSettings]);

  const hasMusicFile = Boolean(videoSettings?.background_music_url);

  function updateMusic(patch: Partial<BackgroundMusicSettings>) {
    setMusic((m) => ({ ...m, ...patch }));
  }
  function updateSubs(patch: Partial<SubtitleSettings>) {
    setSubs((s) => ({ ...s, ...patch }));
  }

  function handleSave() {
    void onSaveMediaSettings({ background_music: music, subtitles: subs });
  }

  const inputCls =
    "mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100";

  // Subtitle preview: approximate the burned-in look.
  const previewBgRgba = hexToRgba(subs.background_color, subs.background_opacity);
  const previewAlign =
    subs.position === "top" ? "items-start" : subs.position === "center" ? "items-center" : "items-end";

  return (
    <div className="space-y-5 p-4">
      {/* ── Música de fondo ── */}
      <section className="space-y-3">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Música de fondo
        </h4>

        <div>
          <label className="block">
            <span className="sr-only">Subir música</span>
            <input
              type="file"
              accept="audio/*"
              onChange={onUploadMusic}
              disabled={musicState === "uploading"}
              className="w-full text-sm text-gray-700 file:mr-3 file:rounded-md file:border-0 file:bg-gray-100 file:px-3 file:py-1.5 file:text-sm file:font-semibold file:text-gray-700 hover:file:bg-gray-200"
            />
          </label>
          <div className="mt-1.5 text-xs">
            {musicState === "uploading" && <p className="text-blue-700">Subiendo música…</p>}
            {musicState === "error" && (
              <p className="text-red-700">{musicError || "Error al subir la música."}</p>
            )}
            {musicState !== "uploading" && hasMusicFile && (
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-green-700">
                  {videoSettings?.background_music_filename || "Música cargada"}
                </span>
                <button
                  type="button"
                  onClick={() => void onDeleteMusic()}
                  className="flex-shrink-0 rounded border border-gray-300 px-2 py-0.5 text-xs font-semibold text-gray-600 hover:bg-gray-50"
                >
                  Quitar
                </button>
              </div>
            )}
            {musicState !== "uploading" && !hasMusicFile && (
              <p className="text-gray-500">
                Sin música. El video se generará sin música de fondo.
              </p>
            )}
          </div>
        </div>

        {/* Loop */}
        <label className="flex items-center justify-between gap-2">
          <span className="text-xs text-gray-700">Repetir música hasta el final del video</span>
          <input
            type="checkbox"
            checked={music.loop}
            onChange={(e) => updateMusic({ loop: e.target.checked })}
            className="h-4 w-4 accent-brand-600"
          />
        </label>

        {/* Volume */}
        <div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-700">Volumen de la música</span>
            <span className="text-xs text-gray-500">{Math.round(music.volume * 100)}%</span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            value={Math.round(music.volume * 100)}
            onChange={(e) => updateMusic({ volume: Number(e.target.value) / 100 })}
            className="mt-1 w-full accent-brand-600"
          />
        </div>

        {/* Fade out */}
        <label className="flex items-center justify-between gap-2">
          <span className="text-xs text-gray-700">Bajar volumen gradualmente al final</span>
          <input
            type="checkbox"
            checked={music.fade_out_enabled}
            onChange={(e) => updateMusic({ fade_out_enabled: e.target.checked })}
            className="h-4 w-4 accent-brand-600"
          />
        </label>
        {music.fade_out_enabled && (
          <label className="block">
            <span className="text-xs text-gray-600">Duración del fade-out (segundos)</span>
            <input
              type="number"
              min={0}
              max={30}
              step={0.5}
              value={music.fade_out_seconds}
              onChange={(e) => updateMusic({ fade_out_seconds: Number(e.target.value) })}
              className={inputCls}
            />
          </label>
        )}
      </section>

      <hr className="border-gray-100" />

      {/* ── Subtítulos ── */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Subtítulos
          </h4>
          <label className="flex items-center gap-2">
            <span className="text-xs text-gray-700">Activar subtítulos</span>
            <input
              type="checkbox"
              checked={subs.enabled}
              onChange={(e) => updateSubs({ enabled: e.target.checked })}
              className="h-4 w-4 accent-brand-600"
            />
          </label>
        </div>

        {subs.enabled && (
          <>
            {/* Live preview */}
            <div
              className={`flex h-20 w-full justify-center overflow-hidden rounded-md border border-gray-200 bg-[linear-gradient(135deg,#e2e8f0,#cbd5e1)] p-2 ${previewAlign}`}
            >
              <span
                style={{
                  fontFamily: subs.font_family,
                  fontSize: `${Math.max(10, Math.round(subs.font_size * 0.6))}px`,
                  color: subs.text_color,
                  backgroundColor: previewBgRgba,
                  padding: "2px 8px",
                  borderRadius: 3,
                  lineHeight: 1.2,
                  maxWidth: "100%",
                }}
              >
                Ejemplo de subtítulo
              </span>
            </div>

            <label className="block">
              <span className="text-xs text-gray-600">Tipografía</span>
              <select
                value={subs.font_family}
                onChange={(e) => updateSubs({ font_family: e.target.value })}
                className={inputCls}
              >
                {FONT_OPTIONS.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </label>

            <div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-600">Tamaño de los subtítulos</span>
                <span className="text-xs text-gray-500">{subs.font_size}px</span>
              </div>
              <input
                type="range"
                min={SUBTITLE_MIN_FONT_SIZE}
                max={SUBTITLE_MAX_FONT_SIZE}
                value={subs.font_size}
                onChange={(e) => updateSubs({ font_size: Number(e.target.value) })}
                className="mt-1 w-full accent-brand-600"
              />
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="text-xs text-gray-600">Color del texto</span>
                <input
                  type="color"
                  value={subs.text_color}
                  onChange={(e) => updateSubs({ text_color: e.target.value })}
                  className="mt-1 h-8 w-full cursor-pointer rounded border border-gray-300 bg-white p-0.5"
                />
              </label>
              <label className="block">
                <span className="text-xs text-gray-600">Color de fondo</span>
                <input
                  type="color"
                  value={subs.background_color}
                  onChange={(e) => updateSubs({ background_color: e.target.value })}
                  className="mt-1 h-8 w-full cursor-pointer rounded border border-gray-300 bg-white p-0.5"
                />
              </label>
            </div>

            <div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-600">Opacidad del fondo</span>
                <span className="text-xs text-gray-500">
                  {Math.round(subs.background_opacity * 100)}%
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={Math.round(subs.background_opacity * 100)}
                onChange={(e) => updateSubs({ background_opacity: Number(e.target.value) / 100 })}
                className="mt-1 w-full accent-brand-600"
              />
            </div>

            <label className="block">
              <span className="text-xs text-gray-600">Posición de subtítulos</span>
              <select
                value={subs.position}
                onChange={(e) => updateSubs({ position: e.target.value as SubtitlePosition })}
                className={inputCls}
              >
                {POSITION_OPTIONS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
      </section>

      <button
        type="button"
        onClick={handleSave}
        disabled={isSaving}
        className="w-full rounded-md bg-brand-600 px-3 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-400"
      >
        {isSaving ? "Guardando…" : "Guardar configuración"}
      </button>
      <div className="min-h-4">
        {saveState === "saved" && (
          <p className="text-xs font-medium text-green-700">Configuración guardada.</p>
        )}
        {saveState === "error" && (
          <p className="text-xs font-medium text-red-700">
            {saveError || "No se pudo guardar la configuración."}
          </p>
        )}
      </div>
    </div>
  );
}

function hexToRgba(hex: string, opacity: number): string {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex);
  if (!m) return `rgba(0,0,0,${opacity})`;
  const n = parseInt(m[1], 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r},${g},${b},${opacity})`;
}
