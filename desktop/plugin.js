import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const DEFAULT_TOPIC = '/camera/image_raw/compressed'
const POLL_MS = 5000
const HEALTH_COLOR = {
  live: '#3dd68c',
  dead_input: '#ff6b6b',
  dead_output: '#e6b35a',
  unmeasured: '#9aa3b5',
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

async function refresh(ctx) {
  const previous = store.snap
  try {
    const settings = await ctx.rest('/settings')
    const graph = await ctx.rest('/graph')
    const failed = graph && graph.ok === false
    store.set({
      settings,
      graph: failed ? null : graph,
      error: failed ? graph.error || 'Graph request failed.' : '',
      at: clock(),
    })
  } catch (err) {
    store.set({
      settings: previous.settings,
      graph: previous.graph,
      error: err && err.message ? err.message : String(err),
      at: clock(),
    })
  }
}

function targetLabel(settings) {
  if (!settings) return 'target unknown'
  return (settings.host || 'local') + ' · ' + (settings.distro || '')
}

function OrphanBanner({ graph }) {
  const orphans = (graph && graph.orphans) || []
  const counts = (graph && graph.counts) || {}
  const count = counts.orphans != null ? counts.orphans : orphans.length
  const excluded = counts.system_excluded || 0
  if (!count && !excluded) return null
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
      background: '#3a2a12',
      color: '#e6b35a',
      padding: '6px 8px',
      marginBottom: '8px',
    },
    children: [
      headline,
      excluded
        ? jsx('div', {
            style: { marginTop: '4px', color: 'var(--ui-text-tertiary)' },
            children: 'System topics were not counted.',
          })
        : null,
    ],
  })
}

function GraphTable({ graph }) {
  const topics = (graph && graph.topics) || []
  if (!topics.length) return jsx('p', { children: 'No topics on this target.' })
  return jsxs('table', {
    style: { width: '100%', borderCollapse: 'collapse', fontSize: '12px' },
    children: [
      jsx('thead', {
        children: jsxs('tr', {
          children: ['topic', 'type', 'pub', 'sub', 'health'].map((label) =>
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

function Pane({ ctx, bus }) {
  const snap = useStore()
  const [polling, setPolling] = useState(false)
  const [topic, setTopic] = useState(() => {
    try {
      return ctx.storage.get('cameraTopic', DEFAULT_TOPIC) || DEFAULT_TOPIC
    } catch (err) {
      return DEFAULT_TOPIC
    }
  })
  const [frame, setFrame] = useState(null)
  const [frameError, setFrameError] = useState('')
  const [busy, setBusy] = useState(false)

  function onTopic(event) {
    const next = event.target.value
    setTopic(next)
    try {
      ctx.storage.set('cameraTopic', next)
    } catch (err) {
      /* the field still works for this session */
    }
  }

  async function grab() {
    setBusy(true)
    setFrameError('')
    try {
      const body = await ctx.rest('/camera?topic=' + encodeURIComponent(topic))
      if (!body || body.ok === false) {
        setFrame(null)
        setFrameError((body && body.error) || 'No frame.')
      } else {
        setFrame(body)
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
          jsx('strong', { children: targetLabel(snap.settings) }),
          jsx('span', {
            style: { color: 'var(--ui-text-tertiary)' },
            children: snap.at ? 'updated ' + snap.at : 'not refreshed',
          }),
        ],
      }),
      jsxs('div', {
        style: { display: 'flex', gap: '6px', marginBottom: '8px' },
        children: [
          jsx('button', { type: 'button', onClick: () => bus.refresh(), children: 'Refresh' }),
          jsx('button', {
            type: 'button',
            onClick: () => {
              const next = !polling
              setPolling(next)
              bus.setPolling(next)
            },
            children: polling ? 'Polling every 5s' : 'Poll',
          }),
        ],
      }),
      snap.error ? jsx('p', { children: snap.error }) : null,
      jsx(OrphanBanner, { graph: snap.graph }),
      snap.graph
        ? jsx(GraphTable, { graph: snap.graph })
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
      id: 'pane',
      area: 'panes',
      title: 'ROS 2',
      data: { placement: 'right', width: '360px' },
      render: () => jsx(Pane, { ctx, bus }),
    })
    ctx.register({
      id: 'orphans',
      area: 'statusBar.right',
      render: () => jsx(Chip, {}),
    })
  },
}
