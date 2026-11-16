// 内联 SVG 图标：20×20 网格，1.7 描边，颜色跟随 currentColor。
const PATHS = {
  inst: '<rect x="2.5" y="3.5" width="15" height="11" rx="2"/><path d="M2.5 7h15M6 17h8"/>',
  proxy:
    '<circle cx="5" cy="5" r="2"/><circle cx="15" cy="15" r="2"/><circle cx="15" cy="5" r="2"/><path d="M7 5h6M15 7v6M6.5 6.5l7 7"/>',
  trash: '<path d="M3.5 5.5h13M8 5.5V4h4v1.5M5 5.5l.8 10.5h8.4L15 5.5M8.5 8.5v5M11.5 8.5v5"/>',
  settings:
    '<path d="M3 6h9M16 6h1M3 14h2M9 14h8"/><circle cx="14" cy="6" r="2"/><circle cx="7" cy="14" r="2"/>',
  plus: '<path d="M10 4v12M4 10h12"/>',
  play: '<path d="M6.5 4.5v11l9-5.5z"/>',
  stop: '<rect x="5" y="5" width="10" height="10" rx="1.5"/>',
  restart: '<path d="M15.5 10a5.5 5.5 0 1 1-1.8-4.1"/><path d="M14.5 2.8v3.6h-3.6"/>',
  edit: '<path d="M12.5 4.5l3 3L8 15H5v-3z"/>',
  open: '<rect x="3" y="3.5" width="14" height="13" rx="3.2"/><path d="M8.5 11.5L12 8M9 8h3v3"/>',
  more: '<path d="M5 10h.01M10 10h.01M15 10h.01" stroke-width="3"/>',
  search: '<circle cx="9" cy="9" r="5"/><path d="M13 13l3.5 3.5"/>',
  chevUD: '<path d="M6.5 8L10 4.5 13.5 8M6.5 12l3.5 3.5 3.5-3.5"/>',
  shield: '<path d="M10 2.8l6 2.4v4.3c0 3.8-2.6 6.4-6 7.7-3.4-1.3-6-3.9-6-7.7V5.2z"/>',
  chevD: '<path d="M5.5 8l4.5 4.5L14.5 8"/>',
  chevR: '<path d="M8 5.5l4.5 4.5L8 14.5"/>',
  check: '<path d="M4.5 10.5l3.5 3.5 7.5-8"/>',
  x: '<path d="M5.5 5.5l9 9M14.5 5.5l-9 9"/>',
  alert: '<path d="M10 3.2l7.5 13H2.5z"/><path d="M10 8.5v3.5M10 14.2h.01"/>',
  info: '<circle cx="10" cy="10" r="7"/><path d="M10 9v4.5M10 6.5h.01"/>',
  okc: '<circle cx="10" cy="10" r="7"/><path d="M6.8 10.2l2.2 2.2 4.2-4.6"/>',
  errc: '<circle cx="10" cy="10" r="7"/><path d="M7.5 7.5l5 5M12.5 7.5l-5 5"/>',
  refresh:
    '<path d="M16 4.5v4h-4M4 15.5v-4h4"/><path d="M15.3 8.5A5.8 5.8 0 0 0 5 6.6M4.7 11.5A5.8 5.8 0 0 0 15 13.4"/>',
  max: '<path d="M3.5 7.5v-4h4M16.5 7.5v-4h-4M3.5 12.5v4h4M16.5 12.5v4h-4"/>',
  min: '<path d="M7.5 3.5v4h-4M12.5 3.5v4h4M7.5 16.5v-4h-4M12.5 16.5v-4h4"/>',
  plug: '<path d="M7 3v4M13 3v4M5 7h10v3a5 5 0 0 1-10 0zM10 15v2.5"/>',
  back: '<path d="M16 10H4.5M9 5l-5 5 5 5"/>',
  fwd: '<path d="M4 10h11.5M11 5l5 5-5 5"/>',
  bell: '<path d="M5 13.5V9a5 5 0 0 1 10 0v4.5l1.5 1.5h-13zM8.2 17a2 2 0 0 0 3.6 0"/>',
  sun: '<circle cx="10" cy="10" r="3.2"/><path d="M10 2.5v1.8M10 15.7v1.8M2.5 10h1.8M15.7 10h1.8M4.7 4.7L6 6M14 14l1.3 1.3M4.7 15.3L6 14M14 6l1.3-1.3"/>',
  moon: '<path d="M16 12.2A6.5 6.5 0 0 1 7.8 4a6.5 6.5 0 1 0 8.2 8.2z"/>',
  box: '<path d="M10 2.8l6.5 3.6v7.2L10 17.2l-6.5-3.6V6.4z"/><path d="M3.5 6.4L10 10l6.5-3.6M10 10v7.2"/>',
  panel: '<rect x="2.5" y="3.5" width="15" height="13" rx="2"/><path d="M7.5 3.5v13"/>',
  panelR: '<rect x="2.5" y="3.5" width="15" height="13" rx="2"/><path d="M12.5 3.5v13"/>',
  lock: '<rect x="4.5" y="9" width="11" height="8" rx="1.5"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9"/>',
  link: '<path d="M8.5 11.5l3-3M7 9.5l-1.7 1.7a2.5 2.5 0 0 0 3.5 3.5l1.7-1.7M13 10.5l1.7-1.7a2.5 2.5 0 0 0-3.5-3.5L9.5 7"/>',
  pulse: '<path d="M2.5 10h3l2-5 3 10 2-5h5"/>',
  restore: '<path d="M4 10a6 6 0 1 0 1.8-4.3M4 3.5v3.2h3.2"/>',
  clock: '<circle cx="10" cy="10" r="7"/><path d="M10 6v4l2.8 1.8"/>',
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg
      className={className ? `cm-icon ${className}` : 'cm-icon'}
      viewBox="0 0 20 20"
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: PATHS[name] }}
    />
  )
}

export function Logo() {
  return (
    <svg viewBox="0 0 26 26" aria-hidden="true">
      <rect className="logo-a" x="2" y="3" width="22" height="8" rx="2.5" />
      <rect className="logo-b" x="2" y="12.5" width="15" height="5" rx="2" />
      <rect className="logo-c" x="2" y="19" width="10" height="4" rx="2" />
    </svg>
  )
}

export const TONE_ICON: Record<string, IconName> = {
  ok: 'okc',
  warn: 'alert',
  danger: 'errc',
  info: 'info',
}
