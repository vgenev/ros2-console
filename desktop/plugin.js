import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'
import { ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'

const DEFAULT_TOPIC = '/camera/image_raw/compressed'

// A read costs 11-12s on the board, so polling has to be slower than a read;
// anything faster only queues work and repaints.
const POLL_MS = 15000

// Animation cycles are quantised: a small rate change between polls would
// otherwise produce a new dur value, restart the SMIL animation, and read as a
// flicker on every refresh.
const ANIM_BUCKETS = ['0.40s', '0.70s', '1.10s', '1.60s', '2.40s']

function animBucket(rate) {
  if (rate >= 20) return 0
  if (rate >= 8) return 1
  if (rate >= 3) return 2
  if (rate >= 1) return 3
  return 4
}
// Muted on purpose: six saturated hues next to each other read as decoration and
// clash with any theme. These stay distinguishable at 1px stroke width while
// sitting quietly on a dark background.
const HEALTH_COLOR = {
  live: '#4fa97e',
  dead_input: '#cf6b70',
  dead_output: '#c8944f',
  unmeasured: '#7f8794',
  state: '#5b93c7',
  silent: '#8b7fc4',
}

const SURFACE = {
  bg: 'var(--ui-bg-primary, #0f1319)',
  stroke: 'var(--ui-stroke-secondary, #ffffff1f)',
  text: 'var(--ui-text-primary, #e6e9ef)',
  muted: 'var(--ui-text-tertiary, #98a1ad)',
  body: 'var(--ui-text-secondary, #c3cad4)',
}

const store = {
  snap: { settings: null, graph: null, error: '', at: '' },
  listeners: new Set(),
  set(next) {
    this.snap = next
    for (const fn of this.listeners) fn(next)
  },
  subscribe(fn) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  },
}

function useStore() {
  const [snap, setSnap] = useState(store.snap)
  useEffect(() => store.subscribe(setSnap), [])
  return snap
}

function clock() {
  return new Date().toLocaleTimeString()
}

// A sample costs 4-6s of SSH, so a hover must not re-fetch the same topic in a
// loop while the pointer wanders across the graph.
const SAMPLE_CACHE = new Map()

async function loadSample(ctx, topic) {
  const hit = SAMPLE_CACHE.get(topic)
  if (hit && Date.now() - hit.at < 30000) return hit.data
  const data = await ctx.rest('/sample?seconds=3&topic=' + encodeURIComponent(topic))
  SAMPLE_CACHE.set(topic, { at: Date.now(), data })
  return data
}

async function refresh(ctx) {
  // A graph read takes 11-12s on real hardware. Polling faster than that stacks
  // requests and repaints the page continuously, which reads as flicker.
  if (refresh.busy) return
  refresh.busy = true
  const previous = store.snap
  store.set(Object.assign({}, previous, { loading: true }))
  try {
    const settings = await ctx.rest('/settings')
    const graph = await ctx.rest('/graph')
    const failed = graph && graph.ok === false
    store.set({
      settings,
      graph: failed ? null : graph,
      error: failed ? graph.error || 'Graph request failed.' : '',
      at: clock(),
      loading: false,
    })
  } catch (err) {
    store.set({
      settings: previous.settings,
      graph: previous.graph,
      error: err && err.message ? err.message : String(err),
      at: clock(),
      loading: false,
    })
  } finally {
    refresh.busy = false
  }
}

function targetLabel(settings) {
  if (!settings) return 'target unknown'
  return (settings.host || 'local') + ' · ' + (settings.distro || '')
}

function LiveDot({ live }) {
  if (!live) return jsx('span', { style: { color: 'var(--ui-text-tertiary)' }, children: '· idle' })
  return jsxs('svg', {
    width: '10',
    height: '10',
    viewBox: '0 0 10 10',
    style: { verticalAlign: 'middle', marginRight: '4px' },
    children: [
      jsx('circle', { cx: '5', cy: '5', r: '4', fill: '#3dd68c' }, 'dot'),
      jsx('animate', {
        attributeName: 'opacity',
        values: '1;0.25;1',
        dur: '1.6s',
        repeatCount: 'indefinite',
      }, 'pulse'),
    ],
  })
}

function OrphanBanner({ graph }) {
  const orphans = (graph && graph.orphans) || []
  const counts = (graph && graph.counts) || {}
  const count = counts.orphans != null ? counts.orphans : orphans.length
  const excluded = counts.system_excluded || 0
  const latched = counts.latched || 0
  if (!count && !excluded && !latched) return null
  const named = orphans
    .slice(0, 3)
    .map((item) => item.name + ' (' + item.health + ')')
    .join(', ')
  const more = orphans.length > 3 ? ' +' + (orphans.length - 3) : ''
  const headline = named
    ? 'Disconnected: ' + count + '. ' + named + more
    : 'Disconnected: ' + count
  return jsxs('div', {
    style: {
      background: 'rgba(200, 148, 79, 0.10)',
      border: '1px solid rgba(200, 148, 79, 0.32)',
      borderRadius: '6px',
      color: SURFACE.body,
      padding: '7px 10px',
      marginBottom: '8px',
      fontSize: '12px',
    },
    children: [
      jsx('span', { style: { color: '#c8944f', fontWeight: 600 }, children: headline }),
      excluded || latched
        ? jsx('div', {
            style: { marginTop: '4px', color: SURFACE.muted, fontSize: '11px' },
            children: [
              excluded ? 'System topics were not counted. ' : '',
              latched
                ? latched + ' latched state topic' + (latched === 1 ? '' : 's') +
                  ' — keeps its last value for late subscribers, so no subscriber is not a fault.'
                : '',
            ].join(''),
          })
        : null,
    ],
  })
}

function TopicCapNote({ graph }) {
  const counts = (graph && graph.counts) || {}
  const hidden = counts.topics_truncated
  if (!hidden) return null
  const shown = counts.topics_returned != null ? counts.topics_returned : 0
  const total = typeof counts.topics === 'number' ? counts.topics : shown + hidden
  return jsx('div', {
    style: {
      color: SURFACE.muted,
      fontSize: '12px',
      marginBottom: '8px',
    },
    children:
      'Showing ' + shown + ' of ' + total +
      ' topics — ' + hidden + ' hidden by the display cap; edges are limited to the shown topics.',
  })
}

// Any of these separates node names in the filter field. Deliberately does NOT
// include "/": node names never contain a slash, but topic names always start
// with one, and splitting on it would turn a pasted topic into two garbage terms.
function splitTerms(text) {
  return String(text || '')
    .split(/[,;|\s]+/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean)
}

// Pure, so it can be tested without a renderer. A term matches a node OR a
// topic: asking for "/demo/verdict" is asking for that wire, and a field
// that silently only knew how to spell node names was a trap.
function selectEdges(edges, opts) {
  const terms = opts.terms || []
  const typeFilter = opts.typeFilter || null
  const focus = opts.focus || null
  const healthOf = opts.healthOf || (() => 'unmeasured')
  const allNodes = [...new Set(edges.map((e) => e.node))]
  const allTopics = [...new Set(edges.map((e) => e.topic))]
  const hit = (name) => terms.some((t) => name.toLowerCase().includes(t))
  const matchedNodes = new Set(terms.length ? allNodes.filter(hit) : [])
  const matchedTopics = new Set(terms.length ? allTopics.filter(hit) : [])

  let shown = edges
  if (typeFilter) shown = shown.filter((e) => healthOf(e.topic) === typeFilter)
  if (terms.length) {
    // A node hit keeps its wires; a topic hit keeps every wire on that topic.
    // Both are "the connections of the thing I named".
    const nodeTopics = new Set(shown.filter((e) => matchedNodes.has(e.node)).map((e) => e.topic))
    shown = shown.filter(
      (e) => matchedNodes.has(e.node) || matchedTopics.has(e.topic) || nodeTopics.has(e.topic)
    )
  }
  const focusTopics = focus
    ? new Set(edges.filter((e) => e.node === focus).map((e) => e.topic))
    : null
  if (focus) shown = shown.filter((e) => e.node === focus || focusTopics.has(e.topic))

  const onMatchedTopic = (n) =>
    shown.some((e) => e.node === n && matchedTopics.has(e.topic)) ||
    edges.some((e) => e.node === n && matchedTopics.has(e.topic))
  const dim = (node) => terms.length > 0 && !matchedNodes.has(node) && !onMatchedTopic(node)

  return {
    shown,
    nodes: [...new Set(shown.map((e) => e.node))].sort(),
    topics: [...new Set(shown.map((e) => e.topic))].sort(),
    matchedNodes,
    matchedTopics,
    dim,
  }
}

function FilterBar({ query, onQuery, showing, total, activeLabel, onClear }) {
  return jsxs('div', {
    style: {
      display: 'flex',
      gap: '8px',
      alignItems: 'center',
      flexWrap: 'wrap',
      padding: '0 2px 8px',
    },
    children: [
      jsx('input', {
        value: query,
        onChange: (ev) => onQuery(ev.target.value),
        placeholder: 'filter nodes or topics — comma, space, semicolon or | separates',
        'aria-label': 'filter nodes by name',
        spellCheck: 'false',
        style: {
          flex: '1 1 260px',
          minWidth: '170px',
          background: 'transparent',
          color: SURFACE.text,
          border: '1px solid ' + SURFACE.stroke,
          borderRadius: '5px',
          padding: '5px 8px',
          fontSize: '12px',
          outline: 'none',
        },
      }, 'q'),
      jsx('span', {
        style: { fontSize: '11px', color: SURFACE.muted, whiteSpace: 'nowrap' },
        children: showing + ' of ' + total + ' nodes',
      }, 'count'),
      activeLabel
        ? jsx('span', {
            onClick: onClear,
            title: 'clear every filter',
            style: {
              fontSize: '11px',
              color: SURFACE.body,
              cursor: 'pointer',
              border: '1px solid ' + SURFACE.stroke,
              borderRadius: '10px',
              padding: '2px 8px',
              whiteSpace: 'nowrap',
            },
            children: activeLabel + '  ×',
          }, 'clear')
        : null,
    ],
  })
}

function Legend({ active, onSelect }) {
  const items = [
    ['live', 'live — connected, data flowing'],
    ['silent', 'silent — connected, nothing in the sample window'],
    ['state', 'state — latched, keeps last value for late subscribers'],
    ['dead_output', 'dead output — published, nobody subscribes'],
    ['dead_input', 'dead input — subscribed, nobody publishes'],
    ['unmeasured', 'unmeasured — no rate measured, not evidence of flow'],
  ]
  return jsxs('div', {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: '6px 10px',
      fontSize: '11px',
      color: SURFACE.muted,
      padding: '8px 4px 2px',
      borderTop: '1px solid ' + SURFACE.stroke,
      marginTop: '4px',
    },
    children: [
      ...items.map(([key, label]) => {
        const on = active === key
        const dim = active && !on
        return jsxs('span', {
          onClick: () => onSelect && onSelect(on ? null : key),
          title: on ? 'clear this filter' : 'show only ' + key + ' topics',
          style: {
            display: 'inline-flex',
            gap: '5px',
            alignItems: 'center',
            cursor: 'pointer',
            padding: '2px 7px',
            borderRadius: '11px',
            opacity: dim ? 0.4 : 1,
            background: on ? 'rgba(255,255,255,0.09)' : 'transparent',
            border: '1px solid ' + (on ? SURFACE.stroke : 'transparent'),
          },
          children: [
            jsx('span', {
              style: {
                width: '9px',
                height: '9px',
                borderRadius: '50%',
                background: HEALTH_COLOR[key],
                display: 'inline-block',
              },
            }, 'sw' + key),
            jsx('span', { children: label }, 'lb' + key),
          ],
        }, key)
      }),
      jsx('span', {
        style: { width: '100%', lineHeight: 1.5 },
        children:
          'solid line = publisher → topic · dashed = topic → subscriber · ' +
          'moving dashes = measured traffic, speed follows msg/s · ' +
          'click a node or a key above to filter',
      }, 'lines'),
    ],
  })
}

function Topology({ graph, onHover, onLeave, onPin }) {
  const edges = (graph && graph.edges) || []
  const topics = (graph && graph.topics) || []
  const [focus, setFocus] = useState(null)
  const [typeFilter, setTypeFilter] = useState(null)
  const [query, setQuery] = useState('')

  if (!edges.length) {
    return jsx('p', {
      children:
        'No edges yet. Edge data needs the rclpy introspection path (it is absent when ' +
        'only the daemon fallback ran).',
    })
  }

  const health = new Map(topics.map((t) => [t.name, t.health]))
  const rates = new Map(topics.map((t) => [t.name, Number(t.rate_hz) || 0]))
  const names = new Set(edges.map((e) => e.node))
  for (const n of (graph && graph.nodes) || []) names.add(n)
  const allNodes = [...names].sort()

  const terms = splitTerms(query)
  const sel = selectEdges(edges, {
    terms,
    typeFilter,
    focus,
    healthOf: (t) => health.get(t) || 'unmeasured',
  })
  const shown = sel.shown
  const matched = sel.matchedNodes
  const isDim = sel.dim
  const focusTopics = focus
    ? new Set(edges.filter((e) => e.node === focus).map((e) => e.topic))
    : null

  const nodeList = [...sel.nodes]
  if (focus && !nodeList.includes(focus)) nodeList.push(focus)
  const topicList = [...sel.topics]

  const rowH = 26
  const W = 820
  const LANE_L = 210
  const LANE_R = W - 210
  const height = Math.max(nodeList.length, topicList.length) * rowH + 24
  const nodeY = new Map(nodeList.map((n, i) => [n, 18 + i * rowH]))
  const topicY = new Map(topicList.map((t, i) => [t, 18 + i * rowH]))

  const lines = shown.map((e, i) => {
    const y1 = nodeY.get(e.node)
    const y2 = topicY.get(e.topic)
    if (y1 == null || y2 == null) return null
    const healthOf = health.get(e.topic)
    const color = HEALTH_COLOR[healthOf] || '#9aa3b5'
    const rate = rates.get(e.topic) || 0
    // Motion is measured traffic, not wiring: the dash cycle shortens as the
    // topic's real message rate rises, and a quiet topic (0 msg/s in the sample
    // window) does not move at all even when both endpoints exist.
    const moves = rate > 0 && (healthOf === 'live' || healthOf === 'silent' || healthOf === 'state')
    const dur = moves ? ANIM_BUCKETS[animBucket(rate)] : null
    const d =
      'M ' + LANE_L + ' ' + y1 + ' C ' + (LANE_L + 110) + ' ' + y1 + ', ' +
      (LANE_R - 110) + ' ' + y2 + ', ' + LANE_R + ' ' + y2
    return jsxs('path', {
      d,
      fill: 'none',
      stroke: color,
      'stroke-width': moves ? 1.8 : 1.2,
      'stroke-dasharray': moves ? '7 7' : e.role === 'publisher' ? '0' : '4 3',
      opacity: (moves ? 0.75 : 0.42) * (isDim(e.node) ? 0.3 : 1),
      children: moves
        ? jsx('animate', {
            attributeName: 'stroke-dashoffset',
            from: '0',
            to: '-28',
            dur: dur,
            repeatCount: 'indefinite',
          })
        : null,
    }, 'e' + i)
  })

  const activeBits = []
  if (typeFilter) activeBits.push('type: ' + typeFilter)
  if (focus) activeBits.push('node: ' + focus)
  if (terms.length) activeBits.push('matching "' + query.trim() + '"')
  const activeLabel = activeBits.join(' · ')

  return jsxs('div', {
    children: [
      jsx(FilterBar, {
        query: query,
        onQuery: setQuery,
        showing: nodeList.length,
        total: allNodes.length,
        activeLabel: activeLabel,
        onClear: () => {
          setQuery('')
          setFocus(null)
          setTypeFilter(null)
        },
      }, 'bar'),
      jsxs('div', {
        style: { overflow: 'auto', border: '1px solid ' + SURFACE.stroke, borderRadius: '6px', background: 'transparent' },
        children: [
          topicList.length === 0
            ? jsx('p', {
                style: { padding: '14px', fontSize: '12px', color: SURFACE.muted },
                children: 'No edge matches these filters — no node and no topic contains that text. Clear it to widen the view.',
              }, 'none')
            : jsxs('svg', {
                width: String(W),
                height: String(Math.max(height, 60)),
                style: { display: 'block' },
                children: [
                  ...lines,
                  ...nodeList.map((n) => {
                    const on = focus === n
                    const peerOfFocus = focusTopics && !on && focusTopics.size > 0 &&
                      edges.some((e) => e.node === n && focusTopics.has(e.topic))
                    return jsx('text', {
                      x: String(LANE_L - 8),
                      y: String(nodeY.get(n) + 4),
                      'text-anchor': 'end',
                      'font-size': '11',
                      fill: on ? SURFACE.text : SURFACE.body,
                      'font-weight': on ? '600' : '400',
                      opacity: isDim(n) ? 0.35 : 1,
                      onClick: () => setFocus(on ? null : n),
                      style: { cursor: 'pointer' },
                      children: (on ? '▸ ' : '') + (n.length > 30 ? n.slice(0, 29) + '…' : n),
                    }, 'n' + n)
                  }),
                  ...topicList.map((t) => {
                    const h = health.get(t)
                    return jsxs('g', {
                      onMouseEnter: (ev) => onHover && onHover(t, ev),
                      onMouseLeave: () => onLeave && onLeave(),
                      onClick: (ev) => onPin && onPin(t, ev),
                      style: { cursor: 'pointer' },
                      children: [
                        jsx('circle', {
                          cx: String(LANE_R + 6),
                          cy: String(topicY.get(t)),
                          r: '3.5',
                          fill: HEALTH_COLOR[h] || '#9aa3b5',
                        }, 'd' + t),
                        jsx('text', {
                          x: String(LANE_R + 15),
                          y: String(topicY.get(t) + 4),
                          'font-size': '11',
                          fill: SURFACE.body,
                          children: t + ' · ' + (rates.get(t) ? rates.get(t).toFixed(1) + '/s' : 'no traffic'),
                        }, 't' + t),
                      ],
                    }, 'g' + t)
                  }),
                ],
              }),
        ],
      }),
      jsx(Legend, { active: typeFilter, onSelect: setTypeFilter }, 'legend'),
    ],
  })
}

// What the read actually found, kept on screen instead of hidden behind a hover:
// counts, and the measurement itself — window and read clock. ROS 2 publishes no
// telemetry, so these numbers exist only because this pane went and measured
// them, and the strip says so.
function MetricsStrip({ graph, at }) {
  if (!graph) return null
  const counts = graph.counts || {}
  const measured = (graph.topics || []).filter((t) => t.measured && t.measured.at)
  const stamp = at || (measured.length ? measured[0].measured.at : '')
  const clock = stamp ? String(stamp).replace('T', ' ').replace('Z', ' UTC') : 'not measured'
  const cells = [
    [counts.nodes, 'nodes'],
    [counts.topics, 'topics'],
    [counts.orphans, 'orphans'],
    [counts.latched, 'latched'],
    [counts.rate_skipped, 'rate skipped'],
  ]
  return jsxs('div', {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: '14px',
      alignItems: 'baseline',
      padding: '6px 8px',
      marginBottom: '8px',
      border: '1px solid var(--ui-border, rgba(255,255,255,0.12))',
      borderRadius: '4px',
      fontFamily: 'ui-monospace, monospace',
      fontSize: '11px',
    },
    children: [
      ...cells.map(([value, label]) =>
        jsxs('span', {
          children: [
            jsx('strong', {
              style: { fontSize: '13px', color: 'var(--ui-text-primary, inherit)' },
              children: value == null ? '—' : String(value),
            }),
            ' ' + label,
          ],
        }, label)
      ),
      jsx('span', {
        style: { marginLeft: 'auto', color: 'var(--ui-text-tertiary)' },
        title: 'when this read was taken; every rate above covers the window named in the table',
        children: 'read ' + clock,
      }),
    ],
  })
}

function GraphTable({ graph, onHover, onLeave, onPin }) {
  const topics = (graph && graph.topics) || []
  if (!topics.length) return jsx('p', { children: 'No topics on this target.' })
  return jsxs('table', {
    style: { width: '100%', borderCollapse: 'collapse', fontSize: '12px' },
    children: [
      jsx('thead', {
        children: jsxs('tr', {
          children: ['topic', 'type', 'pub', 'sub', 'msg/s', 'evidence', 'health'].map((label) =>
            jsx('th', {
              style: {
                textAlign: 'left',
                fontWeight: 600,
                padding: '2px 6px',
                color: 'var(--ui-text-tertiary)',
              },
              children: label,
            }, label)
          ),
        }),
      }),
      jsx('tbody', {
        children: topics.map((topic) =>
          jsxs('tr', {
            onMouseEnter: (ev) => onHover && onHover(topic.name, ev),
            onMouseLeave: () => onLeave && onLeave(),
            onClick: (ev) => onPin && onPin(topic.name, ev),
            style: { cursor: 'pointer' },
            children: [
              jsx('td', { style: { padding: '2px 6px' }, children: topic.name }),
              jsx('td', {
                style: { padding: '2px 6px', color: 'var(--ui-text-tertiary)' },
                children: topic.type,
              }),
              jsx('td', {
                style: { padding: '2px 6px', fontFamily: 'ui-monospace, monospace' },
                children: String(topic.publishers),
              }),
              jsx('td', {
                style: { padding: '2px 6px', fontFamily: 'ui-monospace, monospace' },
                children: String(topic.subscribers),
              }),
              jsx('td', {
                style: { padding: '2px 6px', fontFamily: 'ui-monospace, monospace' },
                title: topic.rate_hz == null ? (topic.rate_reason || 'rate not measured') : undefined,
                children:
                  topic.rate_hz == null
                    ? '—'
                    : Number(topic.rate_hz).toFixed(Number(topic.rate_hz) >= 10 ? 0 : 1),
              }),
              jsx('td', {
                style: {
                  padding: '2px 6px',
                  fontFamily: 'ui-monospace, monospace',
                  color: 'var(--ui-text-tertiary)',
                },
                title: (topic.measured && topic.measured.at)
                  ? 'measured at ' + topic.measured.at
                  : (topic.rate_reason || 'no measurement taken'),
                children: topic.measured
                  ? (topic.measured.msgs == null
                      ? 'unstated'
                      : String(topic.measured.msgs) + ' / ' +
                        String(topic.measured.window_s) + 's')
                  : '—',
              }),
              jsx('td', {
                style: {
                  padding: '2px 6px',
                  color: HEALTH_COLOR[topic.health] || HEALTH_COLOR.unmeasured,
                },
                children: topic.health,
              }),
            ],
          }, topic.name)
        ),
      }),
    ],
  })
}

// ROS messages of type String usually carry JSON (a status blob, a screen of
// state). Pretty-printing it is the difference between a wall of text and
// something you can read, and it is what gets copied.
function prettyPayload(raw) {
  if (!raw) return ''
  let text = String(raw)
  const repr = text.match(/^[A-Za-z0-9_.]*String\(data=(?:'([\s\S]*)'|"([\s\S]*)")?\)$/)
  if (repr) text = repr[1] != null ? repr[1] : repr[2] || ''
  const trimmed = text.trim()
  if (!/^[[{]/.test(trimmed)) return text
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2)
  } catch (err) {
    return text
  }
}

async function copyText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch (err) {
    /* fall through to the textarea path */
  }
  try {
    const area = document.createElement('textarea')
    area.value = text
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  } catch (err) {
    return false
  }
}

function TopicTip({ tip, onClose }) {
  const [copied, setCopied] = useState(false)
  const data = tip.data
  const rawError = tip.error || ''
  const error =
    /404/.test(rawError)
      ? 'This route is not loaded yet — the gateway is running an older plugin build; restart it to enable hover samples.'
      : rawError
  const payload = tip.loading
    ? 'reading one message…'
    : error
      ? error
      : data
        ? data.received
          ? prettyPayload(data.message)
          : data.hint || 'No message arrived in the window.'
        : ''
  const label = { color: '#96a0ae', fontSize: '11px' }

  async function onCopy() {
    const ok = await copyText(payload)
    if (ok) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1400)
    }
  }

  const button = {
    type: 'button',
    onClick: onCopy,
    style: {
      background: copied ? 'rgba(79, 169, 126, 0.22)' : 'rgba(255, 255, 255, 0.08)',
      border: '1px solid rgba(255, 255, 255, 0.16)',
      color: copied ? '#8fd0ac' : '#dfe6ef',
      borderRadius: '5px',
      padding: '2px 8px',
      fontSize: '11px',
      cursor: 'pointer',
      fontFamily: 'inherit',
    },
  }

  return jsxs('div', {
    style: {
      position: 'fixed',
      left: tip.x + 'px',
      top: tip.y + 'px',
      zIndex: 9999,
      width: '520px',
      padding: '10px 12px',
      borderRadius: '7px',
      border: '1px solid rgba(255, 255, 255, 0.16)',
      background: '#11151c',
      color: '#eef2f8',
      boxShadow: '0 12px 34px rgba(0, 0, 0, 0.55)',
      pointerEvents: tip.pinned ? 'auto' : 'none',
      fontSize: '12px',
      lineHeight: '1.5',
    },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '3px' },
        children: [
          jsx('span', {
            style: {
              fontFamily: 'ui-monospace, monospace',
              fontWeight: 700,
              fontSize: '12.5px',
              color: '#ffffff',
              wordBreak: 'break-all',
              flex: '1 1 auto',
            },
            children: tip.topic,
          }, 'name'),
          tip.pinned
            ? jsx('button', button, 'copy')
            : jsx('span', { style: { color: '#96a0ae', fontSize: '10.5px' }, children: 'click to pin' }, 'hint'),
          tip.pinned
            ? jsx('button', {
                type: 'button',
                onClick: onClose,
                style: {
                  background: 'transparent',
                  border: 'none',
                  color: '#96a0ae',
                  cursor: 'pointer',
                  fontSize: '14px',
                  lineHeight: '1',
                  padding: '0 2px',
                },
                children: '×',
              }, 'close')
            : null,
        ],
      }),
      data && data.type
        ? jsx('div', { style: label, children: 'type: ' + data.type }, 'type')
        : null,
      data && data.stamp
        ? jsx('div', { style: label, children: 'stamp: ' + data.stamp }, 'stamp')
        : null,
      jsx('pre', {
        style: {
          margin: '7px 0 0',
          padding: '7px 0 0',
          borderTop: '1px solid rgba(255, 255, 255, 0.10)',
          fontFamily: 'ui-monospace, monospace',
          fontSize: '11.5px',
          lineHeight: '1.5',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          maxHeight: tip.pinned ? '320px' : '180px',
          overflow: 'auto',
          color: error ? '#f0a5a5' : '#dfe6ef',
        },
        children: payload,
      }, 'body'),
    ],
  })
}

function Pane({ ctx, bus }) {
  const snap = useStore()
  // Polling is on by default: the page exists to show a live robot, and a
  // static snapshot is the less useful first impression.
  const [polling, setPolling] = useState(true)
  const [topic, setTopic] = useState(() => {
    try {
      return ctx.storage.get('cameraTopic', '') || DEFAULT_TOPIC
    } catch (err) {
      return DEFAULT_TOPIC
    }
  })
  const [topicTouched, setTopicTouched] = useState(false)
  const [frame, setFrame] = useState(null)
  const [frameError, setFrameError] = useState('')
  const [busy, setBusy] = useState(false)
  const [hostDraft, setHostDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [view, setView] = useState('topology')
  const [tip, setTip] = useState(null)

  // The pane is useless without a target, and an installed user should not have to
  // hand-edit config.yaml to name their robot. Seed once from the saved settings.
  useEffect(() => {
    if (snap.settings && !hostDraft) setHostDraft(snap.settings.host || '')
  }, [snap.settings])

  // Same reasoning for the camera: a hardcoded topic only matches the demo cell.
  // Take the first image topic this graph actually has, until the user types one.
  useEffect(() => {
    if (topicTouched) return
    const topics = (snap.graph && snap.graph.topics) || []
    const image = topics.find((t) => /\/CompressedImage$/.test(t.type || '')) ||
      topics.find((t) => /\/Image$/.test(t.type || ''))
    if (image && image.name !== topic) setTopic(image.name)
  }, [snap.graph, topicTouched])

  // Polling the graph alone feels dead on a live robot: the numbers change but
  // nothing moves. While polling is on, also re-grab the camera, so the picture
  // itself is the liveness signal.
  useEffect(() => {
    if (!polling) return undefined
    const stop = ctx.setInterval(() => {
      if (topic) grab()
    }, 8000)
    return () => stop()
  }, [polling, topic])

  async function save() {
    const base = snap.settings || {}
    setSaving(true)
    setSaveError('')
    try {
      await ctx.rest('/settings', {
        method: 'POST',
        body: {
          host: hostDraft.trim(),
          distro: base.distro || 'jazzy',
          setup: base.setup || '',
          timeout: base.timeout || 15,
        },
      })
      await bus.refresh()
    } catch (err) {
      setSaveError(err && err.message ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  function onTopic(event) {
    const next = event.target.value
    setTopic(next)
    setTopicTouched(true)
    try {
      ctx.storage.set('cameraTopic', next)
    } catch (err) {
      /* the field still works for this session */
    }
  }

  // Start polling when the page is opened and stop when it is left, so nothing
  // reads a robot in the background for a page nobody is looking at.
  useEffect(() => {
    bus.setPolling(true)
    bus.refresh()
    return () => bus.setPolling(false)
  }, [])

  useEffect(() => {
    if (!tip || !tip.pinned) return undefined
    const onKey = (ev) => {
      if (ev.key === 'Escape') setTip(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tip])

  function onPin(topicName, event) {
    // Clicking pins the card so the pointer can travel into it and hit Copy; a
    // hover card with pointerEvents:none can never be interacted with.
    onHover(topicName, event, true)
  }

  function onHover(topicName, event, pin) {
    if (!pin && tip && tip.pinned) return
    const winW = typeof window !== 'undefined' ? window.innerWidth || 1200 : 1200
    const winH = typeof window !== 'undefined' ? window.innerHeight || 800 : 800
    const x = Math.max(8, Math.min(event.clientX + 18, winW - 480))
    // Keep the card inside the window; below the pointer it covers the very label
    // being inspected, so flip above when there is no room underneath.
    const below = event.clientY + 18
    const y = below + 240 > winH ? Math.max(8, event.clientY - 250) : below
    const hit = SAMPLE_CACHE.get(topicName)
    if (hit && Date.now() - hit.at < 30000) {
      setTip({ topic: topicName, data: hit.data, x: x, y: y, pinned: !!pin })
      return
    }
    setTip({ topic: topicName, loading: true, x: x, y: y, pinned: !!pin })
    loadSample(ctx, topicName)
      .then((data) =>
        setTip((cur) =>
          cur && cur.topic === topicName
            ? { topic: topicName, data: data, x: x, y: y, pinned: cur.pinned }
            : cur
        )
      )
      .catch((err) =>
        setTip((cur) =>
          cur && cur.topic === topicName
            ? { topic: topicName, error: String((err && err.message) || err), x: x, y: y }
            : cur
        )
      )
  }

  function onLeave() {
    setTip((cur) => (cur && cur.pinned ? cur : null))
  }

  async function grab() {
    setBusy(true)
    setFrameError('')
    try {
      const body = await ctx.rest('/camera?topic=' + encodeURIComponent(topic))
      if (!body || body.ok === false) {
        setFrame(null)
        setFrameError((body && body.error) || 'No frame.')
      } else if (!frame || frame.data_url !== body.data_url) {
        // Decode the new bitmap before swapping it in. Pointing <img> straight at a
        // fresh data URL blanks the picture for a beat on every poll, which is the
        // most visible part of the flicker.
        if (body.data_url && typeof Image !== 'undefined') {
          const preload = new Image()
          preload.onload = () => setFrame(body)
          preload.onerror = () => setFrame(body)
          preload.src = body.data_url
        } else {
          setFrame(body)
        }
      }
    } catch (err) {
      setFrame(null)
      setFrameError(err && err.message ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return jsxs('div', {
    style: {
      boxSizing: 'border-box',
      height: '100%',
      overflow: 'auto',
      padding: '10px',
      fontSize: '12px',
      color: 'var(--ui-text-secondary)',
    },
    children: [
      jsxs('div', {
        style: { display: 'flex', gap: '8px', alignItems: 'baseline', marginBottom: '8px' },
        children: [
          jsx(LiveDot, { live: polling }),
          jsx('strong', { children: targetLabel(snap.settings) }),
          jsx('span', {
            style: { color: 'var(--ui-text-tertiary)' },
            children: snap.loading
              ? 'reading graph…'
              : snap.at
                ? 'updated ' + snap.at
                : 'not refreshed',
          }),
        ],
      }),
      jsx(MetricsStrip, { graph: snap.graph, at: snap.at }),
      jsxs('div', {
        style: { display: 'flex', gap: '6px', alignItems: 'center', marginBottom: '6px' },
        children: [
          jsx('input', {
            type: 'text',
            value: hostDraft,
            placeholder: 'user@host or ssh alias (empty = this machine)',
            onChange: (event) => setHostDraft(event.target.value),
            onKeyDown: (event) => {
              if (event.key === 'Enter') save()
            },
            style: {
              flex: 1,
              minWidth: 0,
              padding: '2px 6px',
              font: 'inherit',
              color: 'inherit',
              background: 'transparent',
              border: '1px solid var(--ui-stroke-secondary, #8884)',
              borderRadius: '4px',
            },
          }),
          jsx('button', {
            type: 'button',
            disabled: saving,
            onClick: () => save(),
            children: saving ? 'Saving…' : 'Set target',
          }),
        ],
      }),
      saveError
        ? jsx('div', {
            style: { color: 'var(--ui-error-foreground, #ff6b6b)', marginBottom: '6px' },
            children: saveError,
          })
        : null,
      jsxs('div', {
        style: { display: 'flex', gap: '6px', marginBottom: '8px' },
        children: [
          jsx('button', {
            type: 'button',
            disabled: !!snap.loading,
            onClick: () => bus.refresh(),
            children: 'Refresh',
          }),
          jsx('button', {
            type: 'button',
            onClick: () => {
              const next = !polling
              setPolling(next)
              bus.setPolling(next)
            },
            children: polling ? 'Polling every 15s' : 'Poll',
          }),
          jsx('button', {
            type: 'button',
            onClick: () => setView('topology'),
            children: view === 'topology' ? '• Topology' : 'Topology',
          }),
          jsx('button', {
            type: 'button',
            onClick: () => setView('table'),
            children: view === 'table' ? '• Table' : 'Table',
          }),
        ],
      }),
      snap.error ? jsx('p', { children: snap.error }) : null,
      jsx(OrphanBanner, { graph: snap.graph }),
      jsx(TopicCapNote, { graph: snap.graph }),
      snap.graph
        ? view === 'topology'
          ? jsx(Topology, {
              graph: snap.graph,
              onHover: onHover,
              onLeave: onLeave,
              onPin: onPin,
            })
          : jsx(GraphTable, {
              graph: snap.graph,
              onHover: onHover,
              onLeave: onLeave,
              onPin: onPin,
            })
        : snap.error
          ? null
          : jsx('p', { children: 'Waiting for the graph.' }),
      jsxs('div', { style: { marginTop: '12px' }, children: [
        jsx('div', { style: { fontWeight: 600, marginBottom: '6px' }, children: 'Camera' }),
        jsx('input', {
          value: topic,
          onChange: onTopic,
          spellCheck: false,
          style: { width: '100%', boxSizing: 'border-box', marginBottom: '6px' },
        }),
        jsx('button', {
          type: 'button',
          onClick: grab,
          disabled: busy,
          children: busy ? 'Grabbing…' : 'Grab frame',
        }),
        frameError ? jsx('p', { children: frameError }) : null,
        frame && frame.data_url
          ? jsxs('div', {
              children: [
                jsx('img', {
                  alt: topic,
                  src: frame.data_url,
                  style: { display: 'block', width: '100%', height: 'auto', marginTop: '8px' },
                }),
                jsx('div', {
                  style: { marginTop: '4px', fontFamily: 'ui-monospace, monospace' },
                  children: String(frame.bytes) + ' bytes',
                }),
              ],
            })
          : frameError
            ? null
            : jsx('p', { children: 'No frame yet.' }),
      ] }),
      tip ? jsx(TopicTip, { tip: tip, onClose: () => setTip(null) }) : null,
    ],
  })
}

function Chip() {
  const snap = useStore()
  const count = snap.graph && snap.graph.counts ? snap.graph.counts.orphans : '-'
  return jsx('span', {
    style: { font: '11px ui-monospace, monospace', padding: '0 8px' },
    children: 'orphans ' + count,
  })
}

export default {
  id: 'ros2-console',
  name: 'ROS 2',
  register(ctx) {
    let stop = null
    const bus = {
      refresh() {
        return refresh(ctx)
      },
      setPolling(on) {
        if (stop) stop()
        stop = null
        if (on) stop = ctx.setInterval(() => refresh(ctx), POLL_MS)
      },
    }
    ctx.onDispose(() => {
      if (stop) stop()
      store.listeners.clear()
    })
    bus.refresh()
    ctx.register({
      id: 'page',
      area: ROUTES_AREA,
      data: { path: '/ros2-console' },
      render: () => jsx(Pane, { ctx, bus }),
    })
    ctx.register({
      id: 'nav',
      area: SIDEBAR_NAV_AREA,
      data: { path: '/ros2-console', label: 'ROS 2', codicon: 'radio-tower' },
    })
    ctx.register({
      id: 'orphans',
      area: 'statusBar.right',
      render: () => jsx(Chip, {}),
    })
  },
}
