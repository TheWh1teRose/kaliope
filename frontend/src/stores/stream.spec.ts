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
  emit(type: string, data: object, id = '') {
    this.listeners[type]?.({ data: JSON.stringify({ type, ...data }), lastEventId: id } as MessageEvent)
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

  it('reopens a renewed stream at once and quietly, after the last event it saw', () => {
    const states: string[] = []
    const reconnected = vi.fn()
    const events: string[] = []
    openStream({
      url: '/api/series/s1/events',
      types: ['node.started', 'series.completed'],
      terminal: new Set(['series.completed']),
      onEvent: (event) => events.push(event.type),
      onReconnect: reconnected,
      onState: (state) => states.push(state),
      create: (url) => new FakeSource(url) as unknown as EventSource,
      delay: 100,
    })
    const first = FakeSource.made[0]
    first.onopen?.()
    first.emit('node.started', { node: 'plan' }, '41')
    first.emit('stream.renew', {})
    expect(first.closed).toBe(true)
    expect(FakeSource.made).toHaveLength(2)
    expect(FakeSource.made[1].url).toBe('/api/series/s1/events?after=41')
    FakeSource.made[1].onopen?.()
    expect(reconnected).not.toHaveBeenCalled()
    expect(states).not.toContain('reconnecting')
    FakeSource.made[1].emit('series.completed', {}, '42')
    expect(events).toEqual(['node.started', 'series.completed'])
  })

  it('asks a dropped stream for what came after the last event', () => {
    openStream({
      url: '/api/runs/r1/events',
      types: ['node.started'],
      terminal: new Set(),
      onEvent: () => undefined,
      create: (url) => new FakeSource(url) as unknown as EventSource,
      delay: 100,
    })
    FakeSource.made[0].emit('node.started', {}, '7')
    FakeSource.made[0].onerror?.()
    vi.advanceTimersByTime(100)
    expect(FakeSource.made[1].url).toBe('/api/runs/r1/events?after=7')
  })

  it('stops following once nothing drives the job', () => {
    const idle = vi.fn()
    const terminal = vi.fn()
    openStream({
      url: '/api/series/s1/events',
      types: [],
      terminal: new Set(),
      onEvent: () => undefined,
      onTerminal: terminal,
      onIdle: idle,
      create: (url) => new FakeSource(url) as unknown as EventSource,
      delay: 100,
    })
    FakeSource.made[0].emit('stream.idle', {})
    expect(FakeSource.made[0].closed).toBe(true)
    expect(idle).toHaveBeenCalledOnce()
    expect(terminal).not.toHaveBeenCalled()
    FakeSource.made[0].onerror?.()
    vi.advanceTimersByTime(20000)
    expect(FakeSource.made).toHaveLength(1)
  })

  it('treats an idle stream as finished when the page has no idle handler', () => {
    const terminal = vi.fn()
    openStream({
      url: '/x',
      types: [],
      terminal: new Set(),
      onEvent: () => undefined,
      onTerminal: terminal,
      create: (url) => new FakeSource(url) as unknown as EventSource,
    })
    FakeSource.made[0].emit('stream.idle', {})
    expect(terminal).toHaveBeenCalledOnce()
  })
})
