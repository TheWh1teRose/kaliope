/**
 * The readable view of a model answer, independent of its JSON structure.
 *
 * Arrays whose items all carry a string `speaker` and `text` read as dialogue;
 * other strings and numbers become labelled fields; anything else stays a
 * labelled JSON block. So a changed response schema still reads well, and no
 * experiment needs its own renderer for script-shaped output.
 */

export interface DialogueLine {
  speaker: string
  text: string
  kind: string | null
  citations: { blockId: string; quote: string }[]
}

export type TextBlock =
  | { type: 'dialogue'; key: string; lines: DialogueLine[] }
  | { type: 'field'; key: string; value: string }
  | { type: 'json'; key: string; json: string }
  | { type: 'prose'; text: string }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isDialogue(value: unknown): value is Record<string, unknown>[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((v) => isRecord(v) && typeof v.speaker === 'string' && typeof v.text === 'string')
  )
}

function line(entry: Record<string, unknown>): DialogueLine {
  const citations = Array.isArray(entry.citations)
    ? entry.citations.filter(isRecord).map((c) => ({
        blockId: String(c.block_id ?? ''),
        quote: String(c.quote ?? ''),
      }))
    : []
  return {
    speaker: String(entry.speaker),
    text: String(entry.text),
    kind: typeof entry.kind === 'string' ? entry.kind : null,
    citations,
  }
}

/** Blocks for a parsed payload, or for plain text when there is none. */
export function textBlocks(payload: unknown, fallbackText = ''): TextBlock[] {
  if (payload === null || payload === undefined) {
    return fallbackText.trim() ? [{ type: 'prose', text: fallbackText }] : []
  }
  if (typeof payload === 'string') return [{ type: 'prose', text: payload }]
  if (isDialogue(payload)) return [{ type: 'dialogue', key: '', lines: payload.map(line) }]
  if (!isRecord(payload)) return [{ type: 'json', key: '', json: JSON.stringify(payload, null, 2) }]

  const blocks: TextBlock[] = []
  for (const [key, value] of Object.entries(payload)) {
    if (isDialogue(value)) blocks.push({ type: 'dialogue', key, lines: value.map(line) })
    else if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      blocks.push({ type: 'field', key, value: String(value) })
    } else if (value !== null && value !== undefined) {
      blocks.push({ type: 'json', key, json: JSON.stringify(value, null, 2) })
    }
  }
  return blocks
}
