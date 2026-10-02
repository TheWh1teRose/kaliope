import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ModelCatalogue, ModelInfo, ModelSettings as Settings } from '@/api/types'
import { defaultSettings, settingsState } from '@/experiments/modelSettings'
import { t } from '@/i18n'

import ModelSettings from './ModelSettings.vue'

const base: Omit<ModelInfo, 'id'> = {
  provider: 'anthropic',
  input_usd_per_mtok: 3,
  output_usd_per_mtok: 15,
  max_output_tokens: 64_000,
  supports_sampling: true,
  supports_top_k: true,
  sampling_with_thinking: false,
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
    {
      ...base,
      id: 'claude-haiku-4-5',
      max_output_tokens: 32_000,
      thinking_modes: ['budget'],
      effort_levels: [],
      default_effort: null,
    },
    {
      ...base,
      id: 'gpt-6-astra',
      provider: 'openai',
      supports_sampling: false,
      supports_top_k: false,
      thinking_modes: [],
      thinking_default: 'on',
      effort_levels: ['low', 'medium', 'high', 'xhigh', 'max'],
      default_effort: null,
    },
  ],
  providers: [{ name: 'anthropic' }, { name: 'openai' }, { name: 'google' }],
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

  it('caps temperature at 1 for Anthropic and 2 for OpenAI', async () => {
    const anthropic = render(defaultSettings(catalogue.models[0]))
    expect(anthropic.find('#ms-temperature').attributes('max')).toBe('1')
    await anthropic.find('#ms-temperature').setValue('3')
    const capped = anthropic.emitted('update:modelValue')?.at(-1)?.[0] as Settings
    expect(capped.temperature).toBe(1)

    const openai = render(defaultSettings(catalogue.models[2]))
    expect(openai.find('#ms-temperature').attributes('max')).toBe('2')
    await openai.find('#ms-temperature').setValue('3')
    const wider = openai.emitted('update:modelValue')?.at(-1)?.[0] as Settings
    expect(wider.temperature).toBe(2)
  })

  it('clamps top_p into 0–1 and top_k to an integer of at least 1', async () => {
    const wrapper = render(defaultSettings(catalogue.models[0]))
    await wrapper.find('#ms-top-p').setValue('2')
    const topP = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as Settings
    expect(topP.top_p).toBe(1)

    await wrapper.find('#ms-top-k').setValue('0')
    const zero = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as Settings
    expect(zero.top_k).toBe(1)

    await wrapper.find('#ms-top-k').setValue('1.5')
    const rounded = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as Settings
    expect(rounded.top_k).toBe(2)
  })

  it('treats a non-integer thinking budget or max tokens as not valid', async () => {
    const haiku = catalogue.models[3]
    const wrapper = render({ ...defaultSettings(haiku), thinking: 'budget', thinking_budget: 2048 })
    const budget = wrapper.find(`[aria-label="${t.modelSettings.thinkingBudget}"]`)
    await budget.setValue('2048.5')
    const withBudget = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as Settings
    expect(settingsState(haiku, withBudget).valid).toBe(false)

    await wrapper.find('#ms-max-tokens').setValue('8000.5')
    const withTokens = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as Settings
    expect(settingsState(haiku, withTokens).valid).toBe(false)
  })

  it('notes a fractional thinking budget', () => {
    const wrapper = render({
      ...defaultSettings(catalogue.models[3]),
      thinking: 'budget',
      thinking_budget: 2048.5,
    })
    const notes = wrapper.findAll('.note--fail').map((note) => note.text())
    expect(notes).toContain(t.modelSettings.wholeNumber)
    expect(notes).not.toContain(t.modelSettings.budgetTooLow)
    expect(notes).not.toContain(t.modelSettings.budgetNotBelowMax)
  })

  it('names a max tokens value below 1 separately from one above the cap', () => {
    const low = render({ ...defaultSettings(catalogue.models[3]), max_tokens: 0 })
    const lowNotes = low.findAll('.note--fail').map((note) => note.text())
    expect(lowNotes).toContain(t.modelSettings.maxTokensTooLow)
    expect(lowNotes).not.toContain(t.modelSettings.maxTokensTooHigh)

    const high = render({ ...defaultSettings(catalogue.models[3]), max_tokens: 40_000 })
    const highNotes = high.findAll('.note--fail').map((note) => note.text())
    expect(highNotes).toContain(t.modelSettings.maxTokensTooHigh)
    expect(highNotes).not.toContain(t.modelSettings.maxTokensTooLow)
  })

  it('notes a fractional max tokens value', () => {
    const wrapper = render({ ...defaultSettings(catalogue.models[0]), max_tokens: 8000.5 })
    const notes = wrapper.findAll('.note--fail').map((note) => note.text())
    expect(notes).toContain(t.modelSettings.wholeNumber)
    expect(notes).not.toContain(t.modelSettings.maxTokensTooHigh)
  })

  it('names a thinking mode the model actually lists when sampling is off', () => {
    const thinking = render({ ...defaultSettings(catalogue.models[0]), thinking: 'adaptive' })
    expect(thinking.find('.note').text()).toContain('Denken: Aus')

    const haiku = render({
      ...defaultSettings(catalogue.models[3]),
      thinking: 'budget',
      thinking_budget: 2048,
    })
    const note = haiku.find('.note').text()
    expect(note).toContain('Standard des Modells (aus)')
    expect(note).not.toContain('Denken: Aus')
  })

  it('shows German effort labels and keeps the English values', () => {
    const wrapper = render({
      ...defaultSettings(catalogue.models[1]),
      thinking: 'off',
      effort: 'xhigh',
    })
    const byValue = Object.fromEntries(
      wrapper.findAll('#ms-effort option').map((option) => [option.attributes('value'), option.text()]),
    )
    expect(byValue['']).toContain('hoch')
    expect(byValue.low).toBe('niedrig')
    expect(byValue.medium).toBe('mittel')
    expect(byValue.high).toBe('hoch')
    expect(byValue.xhigh).toBe('extra hoch')
    expect(byValue.max).toBe('maximal')
    expect(wrapper.find('.note--fail').text()).toContain('hoch')
    expect(wrapper.find('.note--fail').text()).not.toMatch(/\bhigh\b/)
  })

  it('names no effort level as the default when the provider documents none', () => {
    const wrapper = render(defaultSettings(catalogue.models[4]))
    expect(wrapper.find('#ms-effort option').text()).toBe('Standard')
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
