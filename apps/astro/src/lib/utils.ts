import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

/**
 * Merge Tailwind CSS class names with conflict resolution.
 *
 * Combines clsx (conditional/falsy handling) with tailwind-merge
 * (last-wins Tailwind class resolution). Used by all shadcn/ui components
 * per react.mdx and tailwind.mdx conventions.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
