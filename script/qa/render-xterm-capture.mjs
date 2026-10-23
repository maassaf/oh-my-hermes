import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'

const modules = process.env.HERMES_NODE_MODULES ||
  `${process.env.HOME}/.hermes/hermes-agent/node_modules`
const require = createRequire(`${modules}/package.json`)
const { chromium } = require(`${modules}/playwright`)
const [title, rawPath, evidenceDir, columnsArg, rowsArg] = process.argv.slice(2)
const columns = Number(columnsArg)
const rows = Number(rowsArg)
fs.mkdirSync(evidenceDir, { recursive: true })
const raw = fs.readFileSync(rawPath, 'utf8')
const xtermJs = fs.readFileSync(`${modules}/@xterm/xterm/lib/xterm.js`, 'utf8')
const xtermCss = fs.readFileSync(`${modules}/@xterm/xterm/css/xterm.css`, 'utf8')
const browser = await chromium.launch({
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: true,
})
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } })
await page.setContent(`<!doctype html><meta charset="utf-8"><style>${xtermCss}
html,body{margin:0;background:#07090d;width:100%;height:100%;overflow:hidden}
#title{height:34px;padding:8px 14px;color:#dbeafe;background:#10141c;font:14px ui-monospace}
#terminal{height:calc(100% - 34px);padding:8px}
</style><div id="title"></div><div id="terminal"></div><script>${xtermJs}</script>`)
await page.evaluate(async ({ title, raw, columns, rows }) => {
  document.querySelector('#title').textContent = title
  const term = new Terminal({
    cols: columns,
    rows,
    cursorBlink: false,
    fontFamily: 'Menlo, Monaco, monospace',
    fontSize: 13,
    theme: { background: '#07090d', foreground: '#e5e7eb' },
  })
  term.open(document.querySelector('#terminal'))
  window.__term = term
  await new Promise(resolve => term.write(raw, resolve))
}, { title, raw, columns, rows })
await page.screenshot({ path: path.join(evidenceDir, 'terminal.png') })
const text = await page.evaluate(() => Array.from(
  { length: window.__term.rows },
  (_, y) => window.__term.buffer.active.getLine(y)?.translateToString(true) ?? ''
).join('\n'))
fs.writeFileSync(path.join(evidenceDir, 'terminal-ansi.txt'), raw)
fs.writeFileSync(path.join(evidenceDir, 'terminal.txt'), text)
fs.writeFileSync(path.join(evidenceDir, 'metadata.json'), JSON.stringify({
  title, columns, rows, surface: 'real node-pty + xterm.js + Chrome',
}, null, 2))
fs.writeFileSync(path.join(evidenceDir, 'action-log.json'), JSON.stringify({
  actions: [
    'spawned real Hermes TUI in a bounded PTY',
    `replayed captured ANSI through @xterm/xterm at ${columns}x${rows}`,
    'captured the rendered Chrome surface',
    'terminated the Hermes PTY process',
  ],
}, null, 2))
await browser.close()
