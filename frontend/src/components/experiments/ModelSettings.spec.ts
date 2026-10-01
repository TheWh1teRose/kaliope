import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ModelCatalogue, ModelInfo, ModelSettings as Settings } from '@/api/types'
import { defaultSettings } from '@/experiments/modelSettings'

import ModelSettings from './ModelSettings.vue'

const base: Omit<ModelInfo, 'id'> = {
  provider: 'anthropic',
  input_usd_per_mtok: 3,
  output_usd_per_mtok: 15,
  context_window: 1_000_000,
  max_output_tokens: 64_000,
  small: false,
  supports_sampling: true,
  supports_top_k: true,
  sampling_with_thinking: false,
  supports_structured_outputs: true,
  supports_prompt_caching: true,
  thinking_modes: ['adaptive', 'off'],
  thinking_default: 'off',
  min_thinking_budget: 1024,
  thinking_off_max_effort: null,
  effort_levels: ['low', 'medium', 'high', 'max'],
  default_effort: 'high',
}

const catalogue: ModelCatalogue = {
  models: [
    { ...base, id: 'claude-sonnet-4-6' },
    {
      ...base,
      id: 'claude-opus-5',
      supports_sampling: false,
      supports_top_k: false,
      thinking_default: 'on',
      thinking_off_max_effort: 'high',
      effort_levels: ['low', 'medium', 'high', 'xhigh', 'max'],
    },
    {
      ...base,
      id: 'gpt-4.1',
      provider: 'openai',
      supports_top_k: false,
      thinking_modes: [],
      effort_levels: [],
      default_effort: null,
    },
  ],
  providers: [
    { name: 'anthropic', available: true },
    { name: 'openai', available: false },
    { name: 'google', available: false },
  ],
}

function render(settings: Settings) {
  return mount(ModelSettings, { props: { catalogue, modelValue: settings } })
}

describe('ModelSettings', () => {
  it('enables sampling on a model that samples and is not thinking', () => {
    const wrapper = render(defaultSettings(catalogue.models[0]))
    expect(wrapper.find('#ms-temperature').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('#ms-top-k').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.note').exists()).toBe(false)
  })

  it('disables sampling with a reason on a model that rejects it', () => {
    const wrapper = render(defaultSettings(catalogue.models[1]))
    expect(wrapper.find('#ms-temperature').attributes('disabled')).toBeDefined()
    expect(wrapper.find('#ms-top-p').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.note').text()).toContain('keine Sampling-Parameter')
  })

  it('marks providers without a key as unavailable', () => {
    const wrapper = render(defaultSettings(catalogue.models[0]))
    const openai = wrapper.find('#ms-provider option[value="openai"]')
    expect(openai.attributes('disabled')).toBeDefined()
  })

  it('fits the settings to a newly selected model', async () => {
    const wrapper = render({
      ...defaultSettings(catalogue.models[0]),
      temperature: 0.5,
      effort: 'max',
    })
    await wrapper.find('#ms-model').setValue('claude-opus-5')
    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as Settings
    expect(emitted.model).toBe('claude-opus-5')
    expect(emitted.temperature).toBeNull()
    expect(emitted.effort).toBe('max')
  })
})
