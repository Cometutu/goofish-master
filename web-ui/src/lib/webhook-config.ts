export interface WebhookKeyValueRow {
  id: string
  key: string
  value: string
}

export type WebhookEditorMode = 'pairs' | 'raw'

let rowId = 0

export function createWebhookRow(key = '', value = ''): WebhookKeyValueRow {
  rowId += 1
  return { id: `webhook-row-${rowId}`, key, value }
}

export function parseWebhookRows(rawValue?: string | null): {
  mode: WebhookEditorMode
  rows: WebhookKeyValueRow[]
  raw: string
} {
  const text = normalizeWebhookText(rawValue)
  if (!text) {
    return { mode: 'pairs', rows: [createWebhookRow()], raw: '' }
  }

  const objectValue = parseJsonObject(text) ?? parseKeyValueText(text)
  if (!objectValue) {
    return { mode: 'raw', rows: [createWebhookRow()], raw: text }
  }

  return {
    mode: 'pairs',
    rows: Object.entries(objectValue).map(([key, value]) => createWebhookRow(key, value)),
    raw: text,
  }
}

export function serializeWebhookRows(rows: WebhookKeyValueRow[]): string {
  return rows
    .filter((row) => row.key.trim() || row.value.trim())
    .map((row) => `${row.key.trim()}=${row.value}`)
    .join('\n')
}

export function normalizeWebhookText(rawValue?: string | null): string {
  return String(rawValue ?? '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .trim()
}

function parseJsonObject(text: string): Record<string, string> | null {
  if (!['{', '[', '"'].includes(text[0] ?? '')) {
    return null
  }

  try {
    const parsed = JSON.parse(text)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      return null
    }
    const entries = Object.entries(parsed)
    if (!entries.every(([, value]) => isScalarValue(value))) {
      return null
    }
    return Object.fromEntries(entries.map(([key, value]) => [key, scalarToString(value)]))
  } catch {
    return null
  }
}

function parseKeyValueText(text: string): Record<string, string> | null {
  const lineSegments = text.split('\n').map((segment) => segment.trim()).filter(Boolean)
  if (lineSegments.length > 1) {
    return parseSegments(lineSegments)
  }

  if (text.includes('&')) {
    const searchParams = new URLSearchParams(text)
    const entries = Array.from(searchParams.entries()).filter(([key]) => key.trim())
    if (entries.length > 0) {
      return Object.fromEntries(entries.map(([key, value]) => [key.trim(), value]))
    }
  }

  if (text.includes(';')) {
    const semicolonSegments = text.split(';').map((segment) => segment.trim()).filter(Boolean)
    const parsed = parseSegments(semicolonSegments)
    if (parsed) {
      return parsed
    }
  }

  const pair = parseSegment(text)
  return pair ? { [pair[0]]: pair[1] } : null
}

function parseSegments(segments: string[]): Record<string, string> | null {
  const entries = segments.map(parseSegment)
  if (entries.some((entry) => !entry)) {
    return null
  }
  return Object.fromEntries(entries as [string, string][])
}

function parseSegment(segment: string): [string, string] | null {
  const equalIndex = segment.indexOf('=')
  const colonIndex = segment.indexOf(':')
  const indexes = [equalIndex, colonIndex].filter((index) => index > 0)
  if (indexes.length === 0) {
    return null
  }

  const separatorIndex = Math.min(...indexes)
  const key = segment.slice(0, separatorIndex).trim()
  if (!key) {
    return null
  }
  return [key, segment.slice(separatorIndex + 1).trim()]
}

function isScalarValue(value: unknown): value is string | number | boolean | null {
  return value === null || ['string', 'number', 'boolean'].includes(typeof value)
}

function scalarToString(value: string | number | boolean | null): string {
  return value === null ? '' : String(value)
}
