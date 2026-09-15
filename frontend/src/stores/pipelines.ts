import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type {
  FlowValidation,
  FormatDetail,
  FormatSpec,
  FormatSummary,
  NodeCatalogueOut,
  PipelineDetail,
  PipelineDraft,
  PipelineSummary,
  RevisionOut,
} from '@/api/types'

/**
 * Pipelines, formats and the node catalogue.
 *
 * The node catalogue is cached for the session: it is derived from the running
 * build's node classes, so it cannot change while the console is open. Pipelines
 * and formats are always fetched, because a saved revision must be visible the
 * moment it lands.
 */
export const usePipelinesStore = defineStore('pipelines', () => {
  const pipelines = ref<PipelineSummary[]>([])
  const formats = ref<FormatSummary[]>([])
  const nodes = ref<NodeCatalogueOut | null>(null)

  async function loadNodes(): Promise<NodeCatalogueOut> {
    if (!nodes.value) nodes.value = await api.get<NodeCatalogueOut>('/api/nodes')
    return nodes.value
  }

  async function list(includeArchived = false): Promise<PipelineSummary[]> {
    pipelines.value = await api.get<PipelineSummary[]>(
      `/api/pipelines?include_archived=${includeArchived}`,
    )
    return pipelines.value
  }

  async function get(id: string): Promise<PipelineDetail> {
    return api.get<PipelineDetail>(`/api/pipelines/${id}`)
  }

  async function create(
    payload: PipelineDraft & { id: string; note?: string | null },
  ): Promise<PipelineDetail> {
    return api.post<PipelineDetail>('/api/pipelines', payload)
  }

  async function save(
    id: string,
    payload: PipelineDraft & { note?: string | null },
  ): Promise<PipelineDetail> {
    return api.put<PipelineDetail>(`/api/pipelines/${id}`, payload)
  }

  async function validate(draft: PipelineDraft): Promise<FlowValidation> {
    return api.post<FlowValidation>('/api/pipelines/validate', draft)
  }

  async function archive(id: string, archived: boolean): Promise<PipelineSummary> {
    return api.post<PipelineSummary>(`/api/pipelines/${id}/archive`, { archived })
  }

  async function versions(id: string): Promise<RevisionOut[]> {
    return api.get<RevisionOut[]>(`/api/pipelines/${id}/versions`)
  }

  async function version(id: string, revision: number): Promise<RevisionOut> {
    return api.get<RevisionOut>(`/api/pipelines/${id}/versions/${revision}`)
  }

  async function restore(id: string, revision: number): Promise<PipelineDetail> {
    return api.post<PipelineDetail>(`/api/pipelines/${id}/versions/${revision}/restore`)
  }

  // ---------------------------------------------------------------- formats

  async function listFormats(includeArchived = false): Promise<FormatSummary[]> {
    formats.value = await api.get<FormatSummary[]>(
      `/api/format-specs?include_archived=${includeArchived}`,
    )
    return formats.value
  }

  async function getFormat(id: string): Promise<FormatDetail> {
    return api.get<FormatDetail>(`/api/format-specs/${id}`)
  }

  async function createFormat(spec: FormatSpec, note?: string | null): Promise<FormatDetail> {
    return api.post<FormatDetail>('/api/format-specs', { spec, note })
  }

  async function saveFormat(
    id: string,
    spec: FormatSpec,
    note?: string | null,
  ): Promise<FormatDetail> {
    return api.put<FormatDetail>(`/api/format-specs/${id}`, { spec, note })
  }

  async function archiveFormat(id: string, archived: boolean): Promise<FormatSummary> {
    return api.post<FormatSummary>(`/api/format-specs/${id}/archive`, { archived })
  }

  async function formatVersions(id: string): Promise<RevisionOut[]> {
    return api.get<RevisionOut[]>(`/api/format-specs/${id}/versions`)
  }

  async function restoreFormat(id: string, revision: number): Promise<FormatDetail> {
    return api.post<FormatDetail>(`/api/format-specs/${id}/versions/${revision}/restore`)
  }

  return {
    pipelines,
    formats,
    nodes,
    loadNodes,
    list,
    get,
    create,
    save,
    validate,
    archive,
    versions,
    version,
    restore,
    listFormats,
    getFormat,
    createFormat,
    saveFormat,
    archiveFormat,
    formatVersions,
    restoreFormat,
  }
})
