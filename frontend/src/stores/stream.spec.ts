import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { openStream } from './stream'

class FakeSource {
  static made: FakeSource[] = []
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  listeners: Record<string, (event: MessageEvent) => void> = {}
  closed = false
  constructor(readonly url: string) {
    FakeSource.made.push(this)
  }
  addEventListener(type: string, handler: (event: MessageEvent) => void) {
    this.listeners[type] = handler
  }
  close() {
    this.closed = true
  }
  emit(type: string, data: object) {
    this.listeners[type]?.({ data: JSON.stringify({ type, ...data }) } as MessageEvent)
  }
}

describe('event stream', () => {
  beforeEach(() => {
    FakeSource.made = []
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  it('reopens a dropped stream and tells the page to reload', () => {
    const events: string[] = []
    const reconnected = vi.fn()
    openStream({
      url: '/api/runs/r1/events',
      types: ['node.started', 'run.completed'],
      terminal: new Set(['run.completed']),
      onEvent: (event) => events.push(event.type),
      onReconnect: reconnected,
      create: (url) => new FakeSource(url) as unknown as EventSource,
      delay: 100,
    })
    const first = FakeSource.made[0]
    first.emit('node.started', { node: 'select' })
    first.onerror?.()
    expect(first.closed).toBe(true)
    vi.advanceTimersByTime(100)
    expect(FakeSource.made).toHaveLength(2)
    FakeSource.made[1].onopen?.()
    expect(reconnected).toHaveBeenCalledOnce()
    FakeSource.made[1].emit('run.completed', {})
    expect(events).toEqual(['node.started', 'run.completed'])
    expect(FakeSource.made[1].closed).toBe(true)
  })

  it('does not reopen once closed', () => {
    const handle = openStream({
      url: '/x',
      types: [],
      terminal: new Set(),
      onEvent: () => undefined,
      create: (url) => new FakeSource(url) as unknown as EventSource,
      delay: 100,
    })
    handle.close()
    FakeSource.made[0].onerror?.()
    vi.advanceTimersByTime(1000)
    expect(FakeSource.made).toHaveLength(1)
  })
})
