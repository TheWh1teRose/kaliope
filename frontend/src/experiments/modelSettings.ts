/**
 * What the model settings form allows, derived from the registry (`GET /api/models`).
 *
 * Pure functions, so the rules the form shows are tested without mounting it.
 * They mirror `registry.adapt_parameters` on the backend: a control the model
 * rejects is disabled here, and the provider drops it with a warning anyway.
 */
import { api } from '@/api/client'
import type {
  Effort,
  ModelCatalogue,
  ModelInfo,
  ModelSettings,
  ProviderName,
  ThinkingMode,
} from '@/api/types'

export const EFFORT_ORDER: Effort[] = ['low', 'medium', 'high', 'xhigh', 'max']

export function temperatureLimit(provider: ProviderName): number {
  return provider === 'anthropic' ? 1 : 2
}

export function clampTopP(value: number): number {
  return Math.min(1, Math.max(0, value))
}

export function clampTopK(value: number): number {
  return Math.max(1, Math.round(value))
}

export type Block = 'unsupported' | 'thinking' | null

export interface SettingsState {
  /** Whether the model thinks with these settings, whatever is sent. */
  thinkingOn: boolean
  /** Why temperature and top_p are disabled, or null when they are allowed. */
  samplingBlock: Block
  /** Why top_k is disabled, or null when it is allowed. */
  topKBlock: Block
  /** `default` first, then what the model offers. */
  thinkingModes: ThinkingMode[]
  budgetVisible: boolean
  budgetError: 'tooLow' | 'notBelowMax' | null
  effortLevels: Effort[]
  /** Thinking off at an effort the model only allows with thinking on. */
  thinkingOffEffortError: boolean
  maxTokensCap: number
  maxTokensError: boolean
  /** Mode that turns thinking off so sampling can be changed. `off` when the model lists it. */
  samplingRestore: 'off' | 'default'
  /** Whether a request with these settings would be refused. */
  valid: boolean
}

export function loadModelCatalogue(): Promise<ModelCatalogue> {
  return api.get<ModelCatalogue>('/api/models')
}

export function defaultSettings(model: ModelInfo, maxTokens = 8000): ModelSettings {
  return {
    model: model.id,
    temperature: null,
    top_p: null,
    top_k: null,
    thinking: 'default',
    thinking_budget: null,
    effort: null,
    max_tokens: Math.min(maxTokens, model.max_output_tokens),
  }
}

export function thinkingOn(model: ModelInfo, settings: ModelSettings): boolean {
  if (settings.thinking === 'adaptive' || settings.thinking === 'budget') return true
  return settings.thinking === 'default' && model.thinking_default === 'on'
}

export function settingsState(model: ModelInfo, settings: ModelSettings): SettingsState {
  const on = thinkingOn(model, settings)
  const samplingBlock: Block = !model.supports_sampling
    ? 'unsupported'
    : on && !model.sampling_with_thinking
      ? 'thinking'
      : null
  const topKBlock: Block = !model.supports_top_k ? 'unsupported' : samplingBlock

  const budgetVisible = settings.thinking === 'budget'
  const budget = settings.thinking_budget
  let budgetError: SettingsState['budgetError'] = null
  if (budgetVisible) {
    if (budget === null || budget < model.min_thinking_budget) budgetError = 'tooLow'
    else if (model.provider === 'anthropic' && budget >= settings.max_tokens) {
      budgetError = 'notBelowMax'
    }
  }

  const effort = settings.effort ?? model.default_effort
  const limit = model.thinking_off_max_effort
  const thinkingOffEffortError =
    settings.thinking === 'off' &&
    limit !== null &&
    effort !== null &&
    EFFORT_ORDER.indexOf(effort) > EFFORT_ORDER.indexOf(limit)

  const maxTokensError = !(settings.max_tokens >= 1 && settings.max_tokens <= model.max_output_tokens)
  const topKRefused =
    settings.top_k !== null && (!Number.isInteger(settings.top_k) || settings.top_k < 1)
  const budgetNotWhole =
    settings.thinking_budget !== null && !Number.isInteger(settings.thinking_budget)
  const maxTokensNotWhole = !Number.isInteger(settings.max_tokens)

  return {
    thinkingOn: on,
    samplingBlock,
    topKBlock,
    thinkingModes: ['default', ...model.thinking_modes],
    budgetVisible,
    budgetError,
    effortLevels: model.effort_levels,
    thinkingOffEffortError,
    maxTokensCap: model.max_output_tokens,
    maxTokensError,
    samplingRestore: model.thinking_modes.includes('off') ? 'off' : 'default',
    valid:
      !budgetError &&
      !thinkingOffEffortError &&
      !maxTokensError &&
      !topKRefused &&
      !budgetNotWhole &&
      !maxTokensNotWhole,
  }
}

/**
 * Carry settings over to another model: keep what it accepts, reset the rest
 * to "not sent", and cap `max_tokens` at its output limit.
 */
export function fitToModel(model: ModelInfo, settings: ModelSettings): ModelSettings {
  const next: ModelSettings = { ...settings, model: model.id }
  if (!model.supports_sampling) {
    next.temperature = null
    next.top_p = null
  } else {
    if (next.temperature !== null) {
      next.temperature = Math.min(temperatureLimit(model.provider), Math.max(0, next.temperature))
    }
    if (next.top_p !== null) next.top_p = clampTopP(next.top_p)
  }
  if (!model.supports_top_k) next.top_k = null
  else if (next.top_k !== null) next.top_k = clampTopK(next.top_k)
  if (next.thinking !== 'default' && !model.thinking_modes.includes(next.thinking)) {
    next.thinking = 'default'
  }
  if (next.thinking !== 'budget') next.thinking_budget = null
  if (next.effort !== null && !model.effort_levels.includes(next.effort)) next.effort = null
  next.max_tokens = Math.min(next.max_tokens, model.max_output_tokens)
  return next
}
