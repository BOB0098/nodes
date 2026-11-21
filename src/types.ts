export interface Probe {
  ok: number
  total: number
}

export interface SrcHttp {
  code: number
  ms: number
}

export interface SourceItem {
  tag: string
  note: string
  ok: string[]
  no: string[]
  freq: string
  url: string
  count: number
  probe: Probe
  cnbad: boolean
  src_http: SrcHttp
  age_h: number
  score: number
  streak: number
}

export interface Group {
  title: string
  kern: string
  clients: string
  items: SourceItem[]
}

export interface NodesData {
  updated: string
  ts: number
  raw: number
  errors: string[]
  groups: Group[]
}
