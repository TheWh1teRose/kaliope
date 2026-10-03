/**
 * A server-sent event stream that survives a dropped connection.
 *
 * The browser's EventSource gives up on some errors and retries blindly on
 * others; either way a page that stopped on the first `onerror` kept showing
 * "running" until it was reloaded. This reopens the stream itself with a
 * growing pause and tells the page, so it can reload what it may have missed.
 */

export type StreamState = 'open' | 'reconnecting' | 'closed'

export interface StreamOptions {
  url: string
  /** Event types to listen to. */
  types: string[]
  /** Event types after which the stream is finished. */
  terminal: Set<string>
  onEvent: (event: { type: string } & Record<string, unknown>) => void
  onTerminal?: () => void
  /** Called after a dropped stream is open again. */
  onReconnect?: () => void
  onState?: (state: StreamState) => void
  /** For tests: how to build the EventSource. */
  create?: (url: string) => EventSource
  /** First pause before reconnecting, in ms; doubles up to `maxDelay`. */
  delay?: number
  maxDelay?: number
}

export interface StreamHandle {
  close: () => void
}

export function openStream(options: StreamOptions): StreamHandle {
  const create = options.create ?? ((url: string) => new EventSource(url))
  const first = options.delay ?? 1000
  const max = options.maxDelay ?? 15000
  let delay = first
  let source: EventSource | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let closed = false
  let dropped = false

  const finish = () => {
    closed = true
    if (timer) clearTimeout(timer)
    timer = null
    source?.close()
    source = null
    options.onState?.('closed')
  }

  const connect = () => {
    source = create(options.url)
    source.onopen = () => {
      delay = first
      options.onState?.('open')
      if (dropped) {
        dropped = false
        options.onReconnect?.()
      }
    }
    const handle = (message: MessageEvent) => {
      let payload: { type: string } & Record<string, unknown>
      try {
        payload = JSON.parse(message.data)
      } catch {
        return // a malformed frame is not worth breaking the stream over
      }
      options.onEvent(payload)
      if (options.terminal.has(payload.type)) {
        finish()
        options.onTerminal?.()
      }
    }
    for (const type of options.types) source.addEventListener(type, handle as EventListener)
    source.onerror = () => {
      if (closed) return
      source?.close()
      source = null
      dropped = true
      options.onState?.('reconnecting')
      timer = setTimeout(() => {
        timer = null
        if (!closed) connect()
      }, delay)
      delay = Math.min(delay * 2, max)
    }
  }

  connect()
  return { close: finish }
}
