import { describe, expect, it } from 'vitest'

import type { ModelInfo } from '@/api/types'

import { defaultSettings, fitToModel, settingsState, temperatureLimit } from './modelSettings'

const ALL5 = ['low', 'medium', 'high', 'xhigh', 'max'] as const

function model(overrides: Partial<ModelInfo>): ModelInfo {
  return {
    id: 'm',
    provider: 'anthropic',
    input_usd_per_mtok: 1,
    output_usd_per_mtok: 5,
    max_output_tokens: 64_000,
    supports_sampling: true,
    supports_top_k: true,
    sampling_with_thinking: false,
    thinking_modes: ['adaptive', 'off'],
    thinking_default: 'off',
    min_thinking_budget: 1024,
    thinking_off_max_effort: null,
    effort_levels: [...ALL5],
    default_effort: 'high',
    ...overrides,
  }
}

const opus5 = model({
  id: 'claude-opus-5',
  supports_sampling: false,
  supports_top_k: false,
  thinking_default: 'on',
  thinking_off_max_effort: 'high',
})
const sonnet46 = model({ id: 'claude-sonnet-4-6', effort_levels: ['low', 'medium', 'high', 'max'] })
const haiku = model({
  id: 'claude-haiku-4-5',
  max_output_tokens: 32_000,
  thinking_modes: ['budget'],
  effort_levels: [],
  default_effort: null,
})

describe('settingsState', () => {
  it('disables sampling on a model that rejects it', () => {
    const state = settingsState(opus5, defaultSettings(opus5))
    expect(state.samplingBlock).toBe('unsupported')
    expect(state.topKBlock).toBe('unsupported')
    expect(state.thinkingOn).toBe(true)
  })

  it('allows sampling only while an Anthropic model does not think', () => {
    const off = settingsState(sonnet46, defaultSettings(sonnet46))
    expect(off.samplingBlock).toBeNull()
    expect(off.topKBlock).toBeNull()

    const on = settingsState(sonnet46, { ...defaultSettings(sonnet46), thinking: 'adaptive' })
    expect(on.samplingBlock).toBe('thinking')
    expect(on.topKBlock).toBe('thinking')
  })

  it('keeps sampling while thinking where the provider allows it', () => {
    const gemini = model({ provider: 'google', sampling_with_thinking: true, thinking_default: 'on' })
    expect(settingsState(gemini, defaultSettings(gemini)).samplingBlock).toBeNull()
  })

  it('refuses thinking off above the allowed effort', () => {
    const base = { ...defaultSettings(opus5), thinking: 'off' as const }
    expect(settingsState(opus5, base).thinkingOffEffortError).toBe(false)
    const state = settingsState(opus5, { ...base, effort: 'max' })
    expect(state.thinkingOffEffortError).toBe(true)
    expect(state.valid).toBe(false)
  })

  it('checks the thinking budget against its minimum and max_tokens', () => {
    const base = { ...defaultSettings(haiku), thinking: 'budget' as const }
    expect(settingsState(haiku, { ...base, thinking_budget: 512 }).budgetError).toBe('tooLow')
    expect(
      settingsState(haiku, { ...base, thinking_budget: 8000, max_tokens: 8000 }).budgetError,
    ).toBe('notBelowMax')
    const ok = settingsState(haiku, { ...base, thinking_budget: 2048 })
    expect(ok.budgetError).toBeNull()
    expect(ok.valid).toBe(true)
    expect(ok.effortLevels).toEqual([])
    expect(ok.thinkingModes).toEqual(['default', 'budget'])
  })

  it('flags max_tokens above the model limit', () => {
    const state = settingsState(haiku, { ...defaultSettings(haiku), max_tokens: 40_000 })
    expect(state.maxTokensError).toBe(true)
    expect(state.maxTokensCap).toBe(32_000)
  })

  it('refuses a top_k below 1 and a non-integer budget or max tokens', () => {
    const base = defaultSettings(sonnet46)
    expect(settingsState(sonnet46, { ...base, top_k: 40 }).valid).toBe(true)
    expect(settingsState(sonnet46, { ...base, top_k: 0 }).valid).toBe(false)
    expect(settingsState(sonnet46, { ...base, top_k: 1.5 }).valid).toBe(false)

    const budget = { ...defaultSettings(haiku), thinking: 'budget' as const, thinking_budget: 2048.5 }
    const fractional = settingsState(haiku, budget)
    expect(fractional.budgetError).toBeNull()
    expect(fractional.valid).toBe(false)

    const tokens = settingsState(haiku, { ...defaultSettings(haiku), max_tokens: 8_000.5 })
    expect(tokens.maxTokensError).toBe(false)
    expect(tokens.valid).toBe(false)
  })

  it('names thinking off when the model lists it, otherwise the model default', () => {
    expect(settingsState(sonnet46, defaultSettings(sonnet46)).samplingRestore).toBe('off')
    expect(settingsState(haiku, defaultSettings(haiku)).samplingRestore).toBe('default')
  })
})

describe('fitToModel', () => {
  it('drops what the new model does not accept and caps max_tokens', () => {
    const settings = {
      ...defaultSettings(sonnet46),
      temperature: 0.7,
      top_p: 0.9,
      top_k: 40,
      thinking: 'off' as const,
      effort: 'max' as const,
      max_tokens: 64_000,
    }
    expect(fitToModel(haiku, settings)).toEqual({
      model: 'claude-haiku-4-5',
      temperature: 0.7,
      top_p: 0.9,
      top_k: 40,
      thinking: 'default',
      thinking_budget: null,
      effort: null,
      max_tokens: 32_000,
    })
    const onOpus = fitToModel(opus5, settings)
    expect([onOpus.temperature, onOpus.top_p, onOpus.top_k]).toEqual([null, null, null])
    expect([onOpus.thinking, onOpus.effort]).toEqual(['off', 'max'])
  })

  it('caps temperature at 1 for Anthropic and 2 for the other providers', () => {
    const openai = model({
      id: 'gpt-4.1',
      provider: 'openai',
      supports_top_k: false,
      thinking_modes: [],
      effort_levels: [],
      default_effort: null,
    })
    expect(temperatureLimit('anthropic')).toBe(1)
    expect(temperatureLimit('openai')).toBe(2)
    expect(temperatureLimit('google')).toBe(2)
    const ontoSonnet = fitToModel(sonnet46, { ...defaultSettings(openai), temperature: 1.8 })
    expect(ontoSonnet.temperature).toBe(1)
    const ontoOpenai = fitToModel(openai, { ...defaultSettings(sonnet46), temperature: 1.8 })
    expect(ontoOpenai.temperature).toBe(1.8)
    const clamped = fitToModel(sonnet46, { ...defaultSettings(sonnet46), top_p: 1.4, top_k: 0.2 })
    expect(clamped.top_p).toBe(1)
    expect(clamped.top_k).toBe(1)
  })
})
