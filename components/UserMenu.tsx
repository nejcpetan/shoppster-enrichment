"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Activity, LogOut, Settings } from "lucide-react";

export function UserMenu() {
  const { user, logout } = useAuth();

  const initials =
    user?.full_name
      ?.split(" ")
      .map((n: string) => n[0])
      .join("")
      .slice(0, 2)
      .toUpperCase() ?? "?";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center border border-zinc-700 hover:border-zinc-500 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500/50">
          <span className="text-xs font-bold text-zinc-200">{initials}</span>
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="end"
        className="w-52 bg-zinc-900 border border-zinc-800 text-zinc-100 shadow-xl shadow-black/50"
      >
        <DropdownMenuLabel className="pb-2">
          <div className="text-sm font-semibold text-white leading-tight">
            {user?.full_name ?? "User"}
          </div>
          <div className="text-xs text-zinc-500 font-normal mt-0.5 truncate">
            {user?.email ?? ""}
          </div>
        </DropdownMenuLabel>

        <DropdownMenuSeparator className="bg-zinc-800" />

        <DropdownMenuItem asChild className="cursor-pointer text-zinc-300 hover:text-white focus:text-white focus:bg-zinc-800">
          <Link href="/analytics" className="flex items-center gap-2">
            <Activity className="w-4 h-4" />
            Analytics
          </Link>
        </DropdownMenuItem>

        <DropdownMenuItem asChild className="cursor-pointer text-zinc-300 hover:text-white focus:text-white focus:bg-zinc-800">
          <Link href="/settings" className="flex items-center gap-2">
            <Settings className="w-4 h-4" />
            Settings
          </Link>
        </DropdownMenuItem>

        <DropdownMenuSeparator className="bg-zinc-800" />

        <DropdownMenuItem
          onClick={logout}
          className="cursor-pointer text-zinc-400 hover:text-white focus:text-white focus:bg-zinc-800 gap-2"
        >
          <LogOut className="w-4 h-4" />
          Log out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
