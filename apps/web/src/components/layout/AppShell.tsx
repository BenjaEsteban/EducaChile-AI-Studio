import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

interface AppShellProps {
  title: string;
  children: React.ReactNode;
}

export function AppShell({ title, children }: AppShellProps) {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-auto">
        <Header title={title} />
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
