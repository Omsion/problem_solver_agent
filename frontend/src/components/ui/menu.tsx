import * as React from "react";
import { DropdownMenu as RadixDropdown } from "radix-ui";
import { cn } from "../../lib/utils";

export interface MenuItem {
  label: string;
  onSelect: () => void;
  /** 危险操作（删除）显示为红色 */
  destructive?: boolean;
  disabled?: boolean;
}

interface Props {
  /** 触发按钮内容 */
  trigger: React.ReactNode;
  items: MenuItem[];
  align?: "start" | "center" | "end";
  label?: string;
}

/**
 * 轻量操作菜单（基于 Radix DropdownMenu）。
 *
 * 用途：把卡片上并列的多个图标按钮收敛成一个「更多操作」入口，
 * 避免小屏上按钮挤在一行互相误触。
 */
export const ActionMenu = ({ trigger, items, align = "end", label = "更多操作" }: Props) => (
  <RadixDropdown.Root>
    <RadixDropdown.Trigger
      aria-label={label}
      className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-600 transition-colors cursor-pointer shrink-0"
      onClick={(event) => event.stopPropagation()}
    >
      {trigger}
    </RadixDropdown.Trigger>
    <RadixDropdown.Portal>
      <RadixDropdown.Content
        align={align}
        sideOffset={4}
        className="z-[60] min-w-[10rem] rounded-xl border border-gray-200 bg-white p-1 shadow-lg"
        onClick={(event) => event.stopPropagation()}
      >
        {items.map((item) => (
          <RadixDropdown.Item
            key={item.label}
            disabled={item.disabled}
            onSelect={() => item.onSelect()}
            className={cn(
              "flex cursor-pointer select-none items-center rounded-lg px-3 py-2 text-sm outline-none transition-colors",
              "data-[highlighted]:bg-gray-100",
              item.destructive ? "text-red-600 data-[highlighted]:bg-red-50" : "text-gray-700",
              item.disabled && "pointer-events-none opacity-50",
            )}
          >
            {item.label}
          </RadixDropdown.Item>
        ))}
      </RadixDropdown.Content>
    </RadixDropdown.Portal>
  </RadixDropdown.Root>
);
