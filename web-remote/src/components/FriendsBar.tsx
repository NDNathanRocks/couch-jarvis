import type { DevicePresence } from "../protocol";

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "?";
  const second = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + second).toUpperCase();
}

export function FriendsBar({ devices, selfId }: { devices: DevicePresence[]; selfId: string | null }) {
  const online = devices.filter((d) => d.online);
  if (devices.length === 0) return null;
  return (
    <section aria-label="Connected friends" className="space-y-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
        Connected · {online.length}
      </h2>
      <ul className="flex flex-wrap gap-2">
        {devices.map((d) => (
          <li
            key={d.device_id}
            className={`flex items-center gap-2 rounded-full py-1 pl-1 pr-3 text-sm ring-1 ${
              d.online ? "bg-zinc-900 text-zinc-100 ring-zinc-700" : "bg-zinc-950 text-zinc-500 ring-zinc-900"
            }`}
          >
            <span className="relative flex h-7 w-7 items-center justify-center rounded-full bg-zinc-700 text-xs font-semibold" aria-hidden>
              {initials(d.name)}
              <span className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-zinc-900 ${d.online ? "bg-emerald-400" : "bg-zinc-600"}`} />
            </span>
            <span>
              {d.name}
              {d.device_id === selfId && <span className="ml-1 text-xs text-zinc-500">(you)</span>}
            </span>
            <span className="sr-only">{d.online ? "online" : "offline"}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
