import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { Link, type LinkProps } from 'react-router-dom'
import type { Task } from '../api/client'
import type { BadgeState, Tone } from '../model/status'
import { Icon, TONE_ICON, type IconName } from './Icon'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: string
  icon?: IconName
  busy?: boolean
  small?: boolean
}

export function Button({
  variant,
  icon,
  busy,
  small,
  className = '',
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      type="button"
      {...props}
      disabled={disabled || busy}
      className={`cm-btn${variant ? ` cm-btn--${variant}` : ''}${small ? ' cm-btn--sm' : ''}${busy ? ' is-busy' : ''} ${className}`}
    >
      {busy ? <span className="cm-spin" aria-hidden="true" /> : icon && <Icon name={icon} />}
      {children}
    </button>
  )
}

export function ButtonLink({
  variant,
  icon,
  small,
  className = '',
  children,
  ...props
}: LinkProps & { variant?: string; icon?: IconName; small?: boolean }) {
  return (
    <Link
      {...props}
      className={`cm-btn${variant ? ` cm-btn--${variant}` : ''}${small ? ' cm-btn--sm' : ''} ${className}`}
    >
      {icon && <Icon name={icon} />}
      {children}
    </Link>
  )
}

export function IconButton({
  icon,
  label,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: IconName; label: string }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      {...props}
      className={`cm-icon-btn ${props.className || ''}`}
    >
      <Icon name={icon} />
    </button>
  )
}

export function Notice({
  tone = 'info',
  title,
  children,
  actions,
}: {
  tone?: Tone
  title: string
  children?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className={`cm-notice cm-notice--${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <Icon name={TONE_ICON[tone] || 'info'} />
      <div className="cm-notice__body">
        <span className="cm-notice__title">{title}</span>
        {children && <div>{children}</div>}
        {actions && <div className="cm-notice__actions">{actions}</div>}
      </div>
    </div>
  )
}

export function PageHead({ title, children }: { title: string; children: ReactNode }) {
  return (
    <header className="page-head">
      <h1 className="cm-large-title">{title}</h1>
      <p>{children}</p>
    </header>
  )
}

export function Card({
  title,
  action,
  children,
  footer,
}: {
  title?: string
  action?: ReactNode
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <section className="cm-card">
      {title && (
        <div className="cm-card__head">
          <h2 className="cm-card__title">{title}</h2>
          {action}
        </div>
      )}
      <div className="cm-card__body form">{children}</div>
      {footer && <div className="foot">{footer}</div>}
    </section>
  )
}

export function Field({
  label,
  htmlFor,
  help,
  error,
  required,
  children,
}: {
  label: string
  htmlFor?: string
  help?: ReactNode
  error?: string
  required?: boolean
  children: ReactNode
}) {
  return (
    <div className="cm-field">
      <label className="cm-label" htmlFor={htmlFor}>
        {label}
        {required && (
          <span className="cm-req" aria-hidden="true">
            *
          </span>
        )}
      </label>
      {children}
      {error && (
        <span className="cm-error" id={htmlFor ? `${htmlFor}-error` : undefined}>
          <Icon name="alert" />
          {error}
        </span>
      )}
      {help && <span className="cm-help">{help}</span>}
    </div>
  )
}

export function Empty({
  title,
  children,
  icon = 'inst',
  actions,
}: {
  title: string
  children?: ReactNode
  icon?: IconName
  actions?: ReactNode
}) {
  return (
    <div className="cm-card">
      <div className="cm-empty">
        <Icon name={icon} className="cm-empty__art" />
        <h2 className="cm-empty__title">{title}</h2>
        {children && <p className="cm-muted empty-copy">{children}</p>}
        {actions && <div className="cm-row">{actions}</div>}
      </div>
    </div>
  )
}

export type SelectOption = { value: string; label: string; disabled?: boolean; status?: BadgeState }

function SelectStatus({ state, id }: { state: BadgeState; id?: string }) {
  return (
    <span
      id={id}
      className={`cm-select-status cm-select-status--${state.tone}`}
      title={state.label}
    >
      {state.spin ? (
        <span className="cm-spin" aria-hidden="true" />
      ) : (
        <span className={`cm-dot${state.dot ? ` cm-dot--${state.dot}` : ''}`} aria-hidden="true" />
      )}
      <span className="cm-select-status__label">{state.label}</span>
    </span>
  )
}

/** 浮层挂到 body，避免被表格或滚动容器裁切。焦点保留在 combobox 上。 */
export function Select({
  value,
  options,
  onChange,
  label,
  id,
  disabled,
  placeholder = '请选择',
}: {
  value: string
  options: SelectOption[]
  onChange: (value: string) => void
  label: string
  id?: string
  disabled?: boolean
  placeholder?: string
}) {
  const listId = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const popup = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [position, setPosition] = useState({ top: 0, left: 0, width: 160, maxHeight: 320 })
  const selected = options.findIndex((option) => option.value === value)
  const selectedOption = options[selected]
  const hasStatus = options.some((option) => !!option.status)

  useLayoutEffect(() => {
    if (!open || !trigger.current) return
    const rect = trigger.current.getBoundingClientRect()
    const height = Math.min(options.length * 32 + 12, 320)
    const below = window.innerHeight - rect.bottom - 12
    const above = below < height && rect.top > below
    const maxHeight = Math.max(60, Math.min(320, above ? rect.top - 12 : below))
    const width = Math.min(Math.max(rect.width, hasStatus ? 240 : 170), window.innerWidth - 16)
    setPosition({
      top: above ? Math.max(8, rect.top - Math.min(height, maxHeight) - 5) : rect.bottom + 5,
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
      width,
      maxHeight,
    })
  }, [open, options.length, hasStatus])

  useEffect(() => {
    if (!open) return
    const closeOutside = (event: PointerEvent) => {
      if (
        !trigger.current?.contains(event.target as Node) &&
        !popup.current?.contains(event.target as Node)
      )
        setOpen(false)
    }
    const close = (event: Event) => {
      if (!popup.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', closeOutside)
    window.addEventListener('resize', close)
    document.addEventListener('scroll', close, true)
    return () => {
      document.removeEventListener('pointerdown', closeOutside)
      window.removeEventListener('resize', close)
      document.removeEventListener('scroll', close, true)
    }
  }, [open])

  useEffect(() => {
    if (open)
      popup.current
        ?.querySelector(`[id="${listId}-${active}"]`)
        ?.scrollIntoView({ block: 'nearest' })
  }, [active, open, listId])

  function move(direction: number) {
    const enabled = options
      .map((option, index) => (option.disabled ? -1 : index))
      .filter((index) => index >= 0)
    if (!enabled.length) return
    const current = enabled.indexOf(active)
    setActive(enabled[(current + direction + enabled.length) % enabled.length])
  }

  function choose(index: number) {
    if (options[index] && !options[index].disabled) {
      onChange(options[index].value)
      setOpen(false)
      trigger.current?.focus()
    }
  }

  return (
    <>
      <button
        ref={trigger}
        id={id}
        type="button"
        className="cm-select-btn"
        role="combobox"
        aria-label={label}
        aria-describedby={selectedOption?.status ? `${listId}-status` : undefined}
        title={
          selectedOption?.status
            ? `${selectedOption.label} · ${selectedOption.status.label}`
            : selectedOption?.label
        }
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-haspopup="listbox"
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        disabled={disabled || !options.length}
        onClick={() => {
          setActive(Math.max(0, selected))
          setOpen(!open)
        }}
        onBlur={(event) => {
          if (!popup.current?.contains(event.relatedTarget as Node)) setOpen(false)
        }}
        onKeyDown={(event) => {
          if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key))
            event.preventDefault()
          if (event.key === 'Escape' || event.key === 'Tab') {
            setOpen(false)
            return
          }
          if (!open && ['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
            setActive(Math.max(0, selected))
            setOpen(true)
            return
          }
          if (event.key === 'ArrowDown') move(1)
          if (event.key === 'ArrowUp') move(-1)
          if (event.key === 'Home') setActive(options.findIndex((option) => !option.disabled))
          if (event.key === 'End')
            setActive(options.map((option) => !option.disabled).lastIndexOf(true))
          if (open && ['Enter', ' '].includes(event.key)) choose(active)
        }}
      >
        <span className="cm-select-btn__label">
          <span className="cm-select-btn__name">{selectedOption?.label || placeholder}</span>
          {selectedOption?.status && (
            <SelectStatus state={selectedOption.status} id={`${listId}-status`} />
          )}
        </span>
        <Icon name="chevUD" />
      </button>
      {open &&
        createPortal(
          <div
            ref={popup}
            id={listId}
            className="cm-listbox"
            role="listbox"
            aria-label={label}
            style={position}
            onPointerDown={(event) => event.preventDefault()}
          >
            {options.map((option, index) => (
              <div
                key={option.value}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={value === option.value}
                aria-label={option.status ? `${option.label}，${option.status.label}` : undefined}
                aria-disabled={option.disabled || undefined}
                className={`cm-option${selected === index ? ' is-selected' : ''}${active === index ? ' is-active' : ''}${option.disabled ? ' is-disabled' : ''}`}
                onPointerMove={() => !option.disabled && setActive(index)}
                onClick={() => choose(index)}
              >
                <Icon name="check" />
                <span className="cm-option__label">
                  <span className="cm-option__name" title={option.label}>
                    {option.label}
                  </span>
                  {option.status && <SelectStatus state={option.status} />}
                </span>
              </div>
            ))}
          </div>,
          document.body,
        )}
    </>
  )
}

export function Dialog({
  title,
  children,
  footer,
  onClose,
  locked = false,
}: {
  title: string
  children: ReactNode
  footer: ReactNode
  onClose: () => void
  locked?: boolean
}) {
  const titleId = useId()
  const body = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    body.current?.querySelector<HTMLElement>('input, button:not(:disabled)')?.focus()
    return () => {
      if (opener?.isConnected) opener.focus()
    }
  }, [])
  return createPortal(
    <div
      className="cm-scrim"
      onClick={(event) => {
        if (event.target === event.currentTarget && !locked) onClose()
      }}
    >
      <div
        ref={body}
        className="cm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && !locked) {
            event.stopPropagation()
            closeRef.current()
          }
          if (event.key !== 'Tab') return
          const controls = Array.from(
            body.current?.querySelectorAll<HTMLElement>(
              'input:not(:disabled), button:not(:disabled), a[href], [tabindex="0"]',
            ) || [],
          )
          const first = controls[0],
            last = controls[controls.length - 1]
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault()
            last?.focus()
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault()
            first?.focus()
          }
        }}
      >
        <div className="cm-dialog__head">
          <h2 className="cm-dialog__title" id={titleId}>
            {title}
          </h2>
        </div>
        <div className="cm-dialog__body">{children}</div>
        <div className="cm-dialog__foot">{footer}</div>
      </div>
    </div>,
    document.body,
  )
}

export function ProgressStages({ task }: { task: Task }) {
  const labels: Record<string, string> = {
    done: '完成',
    current: '进行中',
    failed: '失败',
    pending: '等待',
    wait: '等待',
  }
  return (
    <ol className="cm-stages" aria-label="任务进度" aria-live="polite">
      {task.phases.map((phase, index) => (
        <li
          key={phase.name}
          className={`cm-stage is-${phase.state === 'current' ? 'running' : phase.state}`}
        >
          <span className="cm-stage__icon">
            {phase.state === 'current' ? (
              <span className="cm-spin" />
            ) : phase.state === 'done' ? (
              <Icon name="check" />
            ) : phase.state === 'failed' ? (
              <Icon name="x" />
            ) : (
              index + 1
            )}
          </span>
          <span className="cm-stage__label">{phase.name}</span>
          <span className="cm-stage__state">{labels[phase.state] || '等待'}</span>
          {phase.state === 'failed' && task.error && (
            <span className="cm-stage__detail">{task.error}</span>
          )}
        </li>
      ))}
    </ol>
  )
}
