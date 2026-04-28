import { AppShell } from "@/components/layout/AppShell";

const stats = [
  { label: "Videos generados", value: "0" },
  { label: "Presentaciones subidas", value: "0" },
  { label: "Tareas en cola", value: "0" },
];

export default function DashboardPage() {
  return (
    <AppShell title="Dashboard">
      <div className="mb-6">
        <h2 className="text-xl font-semibold text-gray-900">Bienvenido</h2>
        <p className="mt-1 text-sm text-gray-500">
          Resumen de actividad de tu plataforma.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {stats.map(({ label, value }) => (
          <div
            key={label}
            className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
          >
            <p className="text-sm text-gray-500">{label}</p>
            <p className="mt-1 text-3xl font-bold text-gray-900">{value}</p>
          </div>
        ))}
      </div>
    </AppShell>
  );
}
