interface HeaderProps {
  title: string;
}

export function Header({ title }: HeaderProps) {
  return (
    <header className="flex h-14 items-center border-b border-gray-200 bg-white px-6">
      <h1 className="text-sm font-semibold text-gray-800">{title}</h1>
    </header>
  );
}
