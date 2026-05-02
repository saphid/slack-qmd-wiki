#!/usr/bin/env node
import http from 'node:http';
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const HOST = process.env.LLM_WIKI_SEARCH_HOST || '127.0.0.1';
const PORT = Number(process.env.LLM_WIKI_SEARCH_PORT || 8765);
const QMD = process.env.QMD_BIN || 'qmd';
const ROOT = process.env.LLM_WIKI_ROOT || process.cwd();
const SLACK_RUN_ID = process.env.SLACK_RUN_ID || process.env.LLM_WIKI_SLACK_RUN_ID || detectSlackRunId();
const MAX_QUERY_CHARS = Number(process.env.LLM_WIKI_SEARCH_MAX_QUERY_CHARS || 1000);
const MAX_RESULTS = Number(process.env.LLM_WIKI_SEARCH_MAX_RESULTS || 500);
const FACET_CACHE_MS = Number(process.env.LLM_WIKI_FACET_CACHE_MS || 300000);
function detectSlackRunId() {
  const statePath = path.join(ROOT, '.state/slack-chunk-download-state.json');
  try {
    if (existsSync(statePath)) {
      const state = JSON.parse(readFileSync(statePath, 'utf8'));
      if (state?.run_id) return String(state.run_id);
    }
  } catch {
    // Fall back to the portable default below.
  }
  return 'all-feeds';
}

const COLLECTIONS = {
  raw: ['slack-raw'],
  slack: ['slack-raw'],
  chunks: ['slack-api-chunks'],
  wiki: ['llm-wiki'],
  all: ['slack-api-chunks', 'slack-raw', 'llm-wiki'],
};
const CORPUS_TO_COLLECTION_PARAM = {
  raw: 'raw',
  slack: 'raw',
  chunks: 'chunks',
  api: 'chunks',
  'api-chunks': 'chunks',
  wiki: 'wiki',
  all: 'all',
};

let facetCache = {expires: 0, data: null};

function json(res, status, body) {
  res.writeHead(status, {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'});
  res.end(JSON.stringify(body));
}
function text(res, status, body, contentType = 'text/plain; charset=utf-8') {
  res.writeHead(status, {'content-type': contentType, 'cache-control': 'no-store'});
  res.end(body);
}
function stripQuotes(value) { return String(value || '').trim().replace(/^['"]|['"]$/g, ''); }
function normName(value) { return String(value || '').trim().replace(/^#/, '').toLowerCase(); }
function normalizeChannel(value) { return stripQuotes(value).replace(/^#/, '').trim(); }
function collectionsFrom(value) {
  const requested = String(value || 'all').split(',').map((s) => s.trim()).filter(Boolean);
  const mapped = [];
  for (const item of requested) {
    if (!COLLECTIONS[item]) throw new Error(`unknown collection: ${item}`);
    mapped.push(...COLLECTIONS[item]);
  }
  return [...new Set(mapped)];
}

function runQmd(args, timeoutMs = 120000) {
  return new Promise((resolve) => {
    const child = spawn(QMD, args, {stdio: ['ignore', 'pipe', 'pipe']});
    let stdout = ''; let stderr = '';
    const timer = setTimeout(() => { child.kill('SIGTERM'); setTimeout(() => child.kill('SIGKILL'), 2000).unref(); }, timeoutMs);
    child.stdout.on('data', (d) => { stdout += d.toString(); });
    child.stderr.on('data', (d) => { stderr += d.toString(); });
    child.on('close', (code, signal) => { clearTimeout(timer); resolve({code, signal, stdout, stderr}); });
    child.on('error', (error) => { clearTimeout(timer); resolve({code: 127, signal: null, stdout, stderr: String(error)}); });
  });
}

function parseDecorators(query) {
  const decorators = {};
  const pattern = /(^|\s)(from|user|in|channel|after|before|on|corpus|mode|sort):("[^"]+"|'[^']+'|[^\s]+)/gi;
  const cleanQuery = String(query || '').replace(pattern, (_m, prefix, key, rawValue) => {
    const value = stripQuotes(rawValue);
    const k = key.toLowerCase();
    if (k === 'from' || k === 'user') decorators.user = value;
    else if (k === 'in' || k === 'channel') decorators.channel = normalizeChannel(value);
    else if (k === 'after') decorators.dateFrom = value;
    else if (k === 'before') decorators.dateTo = value;
    else if (k === 'on') { decorators.dateFrom = value; decorators.dateTo = value; }
    else if (k === 'corpus') decorators.collection = CORPUS_TO_COLLECTION_PARAM[value.toLowerCase()] || value.toLowerCase();
    else if (k === 'mode') decorators.mode = value.toLowerCase();
    else if (k === 'sort') {
      const v = value.toLowerCase();
      decorators.sort = ({newest: 'date-desc', oldest: 'date-asc', date: 'date-desc'}[v] || v);
    }
    return prefix || ' ';
  }).replace(/\s+/g, ' ').trim();
  return {cleanQuery, decorators};
}

function effectiveParams(params) {
  const parsed = parseDecorators(params.get('q') || '');
  const effective = new URLSearchParams(params);
  effective.set('q', parsed.cleanQuery);
  for (const [key, value] of Object.entries(parsed.decorators)) {
    if (value && !String(effective.get(key) || '').trim()) effective.set(key, value);
  }
  return {effective, decorators: parsed.decorators, cleanQuery: parsed.cleanQuery};
}

function parseMeta(result) {
  const file = String(result.file || result.path || '');
  const title = String(result.title || '');
  const meta = { corpus: 'unknown', date: '', channel: '', path: file };
  if (file.startsWith('qmd://slack-raw/')) {
    meta.corpus = 'raw Slack';
    const parts = file.replace('qmd://slack-raw/', '').split('/');
    if (/^\d{4}-\d{2}-\d{2}$/.test(parts[0] || '')) meta.date = parts[0];
    if (parts[1]) meta.channel = parts[1].replace(/\.md$/, '');
  } else if (file.startsWith('qmd://slack-api-chunks/')) {
    meta.corpus = 'Slack API chunks';
    const parts = file.replace('qmd://slack-api-chunks/', '').split('/');
    if (parts[1]) meta.channel = parts[1];
  } else if (file.startsWith('qmd://llm-wiki/')) {
    meta.corpus = 'wiki';
    meta.channel = file.replace('qmd://llm-wiki/', '').split('/')[0] || '';
  }
  const titleMatch = title.match(/#([^\s]+) on (\d{4}-\d{2}-\d{2})/);
  if (titleMatch) { meta.channel ||= titleMatch[1]; meta.date ||= titleMatch[2]; }
  return meta;
}
function enrich(results) { return results.map((r) => ({...r, meta: parseMeta(r)})); }

function applyFilters(results, params) {
  const channel = normalizeChannel(params.get('channel') || '').toLowerCase();
  const dateFrom = String(params.get('dateFrom') || '').trim();
  const dateTo = String(params.get('dateTo') || '').trim();
  const within = String(params.get('within') || '').trim().toLowerCase();
  return results.filter((r) => {
    const date = r.meta?.date || '';
    if (channel && !String(r.meta?.channel || '').toLowerCase().includes(channel)) return false;
    if (dateFrom && (!date || date < dateFrom)) return false;
    if (dateTo && (!date || date > dateTo)) return false;
    const haystack = `${r.title || ''}\n${r.file || ''}\n${r.snippet || ''}\n${r.context || ''}\n${r.meta?.channel || ''}`.toLowerCase();
    if (within && !haystack.includes(within)) return false;
    return true;
  });
}
function sortResults(results, sort) {
  const copy = [...results];
  if (sort === 'date-desc') copy.sort((a, b) => String(b.meta?.date || '').localeCompare(String(a.meta?.date || '')) || Number(b.score || 0) - Number(a.score || 0));
  else if (sort === 'date-asc') copy.sort((a, b) => String(a.meta?.date || '').localeCompare(String(b.meta?.date || '')) || Number(b.score || 0) - Number(a.score || 0));
  else if (sort === 'channel') copy.sort((a, b) => String(a.meta?.channel || '').localeCompare(String(b.meta?.channel || '')) || String(a.meta?.date || '').localeCompare(String(b.meta?.date || '')));
  else if (sort === 'corpus') copy.sort((a, b) => String(a.meta?.corpus || '').localeCompare(String(b.meta?.corpus || '')) || String(a.file || '').localeCompare(String(b.file || '')));
  return copy;
}

function localMarkdownPathFromQmd(file) {
  const value = String(file || '');
  if (value.startsWith('qmd://slack-raw/')) return path.join(ROOT, 'raw/slack', value.replace('qmd://slack-raw/', ''));
  if (value.startsWith('qmd://slack-api-chunks/')) return path.join(ROOT, 'qmd/slack-api-chunks', value.replace('qmd://slack-api-chunks/', ''));
  return '';
}
function headingUser(line) {
  const m = String(line || '').match(/^##\s+[^|\n]+\|\s+([^|\n]+?)\s+\|\s+ts=/);
  return m ? m[1].trim() : '';
}
function lineNumberedSnippet(blocks) {
  const lines = [];
  for (const block of blocks.slice(0, 4)) {
    for (let i = 0; i < block.lines.length && i < 16; i++) {
      lines.push(String(block.startLine + i) + ': ' + block.lines[i]);
    }
    lines.push('');
    if (lines.length > 56) break;
  }
  return lines.join('\n').trim();
}
async function exactUserBlocks(result, user) {
  const file = localMarkdownPathFromQmd(result.file || result.path || '');
  if (!file) return null;
  const wanted = normName(user);
  if (!wanted) return null;
  let body;
  try { body = await readFile(file, 'utf8'); } catch { return null; }
  const lines = body.split(/\r?\n/);
  const blocks = [];
  let current = null;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].startsWith('## ')) {
      if (current) blocks.push(current);
      current = {startLine: i + 1, user: headingUser(lines[i]), lines: [lines[i]]};
    } else if (current) current.lines.push(lines[i]);
  }
  if (current) blocks.push(current);
  const matches = blocks.filter((block) => normName(block.user) === wanted);
  return matches;
}
async function applyExactUserSnippets(results, params) {
  const user = stripQuotes(params.get('user') || '');
  if (!user) return results;
  const out = [];
  for (const result of results) {
    const blocks = await exactUserBlocks(result, user);
    if (blocks === null) continue;
    if (!blocks.length) continue;
    out.push({...result, snippet: lineNumberedSnippet(blocks), exactUserMatches: blocks.length});
  }
  return out;
}

function buildSearchArgs(originalParams) {
  const {effective, decorators, cleanQuery} = effectiveParams(originalParams);
  let query = cleanQuery || stripQuotes(effective.get('user') || '') || normalizeChannel(effective.get('channel') || '') || 'source slack';
  if (query.length > MAX_QUERY_CHARS) throw new Error(`query too long; max ${MAX_QUERY_CHARS} chars`);
  const mode = String(effective.get('mode') || 'lex');
  const requestedLimit = Math.min(Math.max(Number(effective.get('n') || 25), 1), MAX_RESULTS);
  const collections = collectionsFrom(effective.get('collection'));
  const hasPostFilter = ['channel', 'user', 'dateFrom', 'dateTo', 'within'].some((key) => String(effective.get(key) || '').trim());
  const fetchLimit = hasPostFilter ? Math.min(MAX_RESULTS, Math.max(requestedLimit * 5, 100)) : requestedLimit;

  const user = stripQuotes(effective.get('user') || '');
  if (mode === 'lex' && user && !query.toLowerCase().includes(user.toLowerCase())) query = `${query} "${user}"`;

  let args;
  if (mode === 'lex') args = ['search', query];
  else if (mode === 'vec') args = ['vsearch', query];
  else if (mode === 'hybrid') {
    const q = /^(lex|vec|hyde|intent|expand):/m.test(query) ? query : `expand: ${query}`;
    args = ['query', q, '-C', String(Math.min(Math.max(Number(effective.get('candidateLimit') || 20), 1), 100))];
    if (String(effective.get('rerank') || '') !== '1') args.push('--no-rerank');
  } else throw new Error(`unknown mode: ${mode}`);
  args.push('-n', String(fetchLimit), '--json', '--line-numbers');
  for (const c of collections) args.push('-c', c);
  return {args, requestedLimit, fetchLimit, effective, decorators, cleanQuery: query};
}

async function readJsonIfExists(file, fallback) {
  try { return JSON.parse(await readFile(file, 'utf8')); } catch { return fallback; }
}
async function listRawMarkdown(dir, out = []) {
  let entries = [];
  try { entries = await readdir(dir, {withFileTypes: true}); } catch { return out; }
  for (const ent of entries) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) await listRawMarkdown(p, out);
    else if (ent.isFile() && ent.name.endsWith('.md')) out.push(p);
  }
  return out;
}
async function buildFacets() {
  const now = Date.now();
  if (facetCache.data && facetCache.expires > now) return facetCache.data;
  const channels = new Map();
  const users = new Map();
  const conversations = await readJsonIfExists(path.join(ROOT, 'chunks/slack', SLACK_RUN_ID, 'conversations.json'), []);
  for (const c of Array.isArray(conversations) ? conversations : []) {
    const name = c.name || c.name_normalized || c.id;
    if (!name) continue;
    channels.set(name, {name, id: c.id || '', type: c.is_im ? 'im' : c.is_mpim ? 'mpim' : c.is_group ? 'private' : 'channel', archived: !!c.is_archived, count: channels.get(name)?.count || 0});
  }
  const profiles = await readJsonIfExists(path.join(ROOT, 'chunks/slack', SLACK_RUN_ID, 'user_profiles.json'), {});
  const aliasToUserName = new Map();
  function addUser(item) {
    if (!item || !item.name) return;
    const existing = users.get(item.name) || {};
    const merged = {...existing, ...item, count: existing.count || item.count || 0};
    users.set(item.name, merged);
    for (const alias of (merged.aliases || [])) if (alias) aliasToUserName.set(normName(alias), merged.name);
  }
  for (const [id, profile] of Object.entries(profiles || {})) {
    const displayName = profile.display_name || profile.display_name_normalized || '';
    const realName = profile.real_name || profile.real_name_normalized || '';
    const slackName = profile.name || '';
    const canonical = displayName || realName || slackName || id;
    const aliases = [...new Set([canonical, displayName, realName, slackName, id].filter(Boolean))];
    addUser({name: canonical, id, displayName, realName, slackName, title: profile.title || '', deleted: !!profile.deleted, isBot: !!profile.is_bot, aliases, label: realName && normName(realName) !== normName(canonical) ? `${canonical} — ${realName}` : canonical});
  }
  const userMap = await readJsonIfExists(path.join(ROOT, 'chunks/slack', SLACK_RUN_ID, 'users.json'), {});
  for (const [id, name] of Object.entries(userMap || {})) {
    if (!name) continue;
    const canonical = aliasToUserName.get(normName(name));
    if (canonical) continue;
    addUser({name: String(name), id, displayName: String(name), realName: '', slackName: '', aliases: [String(name), id], label: String(name)});
  }

  const files = await listRawMarkdown(path.join(ROOT, 'raw/slack'));
  const dateSet = new Set();
  for (const file of files) {
    const rel = path.relative(path.join(ROOT, 'raw/slack'), file);
    const parts = rel.split(path.sep);
    if (/^\d{4}-\d{2}-\d{2}$/.test(parts[0] || '')) dateSet.add(parts[0]);
    if (parts[1]) {
      const ch = parts[1].replace(/\.md$/, '');
      const current = channels.get(ch) || {name: ch, id: '', type: 'channel', archived: false, count: 0};
      current.count += 1; channels.set(ch, current);
    }
    try {
      const body = await readFile(file, 'utf8');
      for (const m of body.matchAll(/^##\s+[^|\n]+\|\s+([^|\n]+?)\s+\|\s+ts=/gm)) {
        const name = m[1].trim();
        if (!name) continue;
        const canonical = aliasToUserName.get(normName(name)) || name;
        const current = users.get(canonical) || {name: canonical, id: '', aliases: [canonical], label: canonical, count: 0};
        current.count = (current.count || 0) + 1; users.set(canonical, current);
      }
    } catch {}
  }
  const dates = [...dateSet].sort();
  const data = {
    channels: [...channels.values()].sort((a, b) => (b.count || 0) - (a.count || 0) || a.name.localeCompare(b.name)),
    users: [...users.values()].sort((a, b) => (b.count || 0) - (a.count || 0) || a.name.localeCompare(b.name)),
    dates: {min: dates[0] || '', max: dates.at(-1) || ''},
    decorators: ['from:"Jordan Example"', 'user:Jamie', 'in:#general', 'channel:random', 'after:2026-01-01', 'before:2026-02-01', 'on:2026-02-26', 'corpus:wiki', 'corpus:raw', 'mode:hybrid', 'sort:newest'],
  };
  facetCache = {expires: now + FACET_CACHE_MS, data};
  return data;
}

async function handleSearch(req, res, url) {
  let built;
  try { built = buildSearchArgs(url.searchParams); }
  catch (error) { return json(res, 400, {error: error.message}); }
  const timeout = built.args[0] === 'query' ? 180000 : 60000;
  const result = await runQmd(built.args, timeout);
  if (result.code !== 0) return json(res, 500, {error: 'qmd failed', code: result.code, signal: result.signal, stderr: result.stderr.slice(-4000)});
  try {
    const parsed = JSON.parse(result.stdout || '[]');
    const enriched = enrich(parsed);
    const filtered = applyFilters(enriched, built.effective);
    const exactUserFiltered = await applyExactUserSnippets(filtered, built.effective);
    const sorted = sortResults(exactUserFiltered, String(built.effective.get('sort') || 'relevance'));
    const limited = sorted.slice(0, built.requestedLimit);
    return json(res, 200, {args: built.args, decorators: built.decorators, effectiveQuery: built.cleanQuery, maxResults: MAX_RESULTS, fetched: parsed.length, matched: exactUserFiltered.length, returned: limited.length, results: limited});
  } catch (error) {
    return json(res, 500, {error: 'qmd returned non-json output', stdout: result.stdout.slice(0, 4000), stderr: result.stderr.slice(-4000)});
  }
}
async function handleGet(req, res, url) {
  const target = String(url.searchParams.get('target') || '').trim();
  const lines = Math.min(Math.max(Number(url.searchParams.get('lines') || 160), 1), 1000);
  if (!target.startsWith('qmd://') && !target.startsWith('#')) return json(res, 400, {error: 'target must be a qmd:// path or #docid'});
  const result = await runQmd(['get', target, '-l', String(lines)], 60000);
  if (result.code !== 0) return json(res, 500, {error: 'qmd get failed', stderr: result.stderr.slice(-4000)});
  return text(res, 200, result.stdout);
}

const htmlPath = path.join(path.dirname(fileURLToPath(import.meta.url)), 'qmd-search.html');
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://${req.headers.host || `${HOST}:${PORT}`}`);
  try {
    if (url.pathname === '/' || url.pathname === '/index.html') return text(res, 200, await readFile(htmlPath, 'utf8'), 'text/html; charset=utf-8');
    if (url.pathname === '/health') return json(res, 200, {ok: true, service: 'llm-wiki-qmd-search', qmd: QMD, maxResults: MAX_RESULTS});
    if (url.pathname === '/api/facets') return json(res, 200, await buildFacets());
    if (url.pathname === '/api/search') return await handleSearch(req, res, url);
    if (url.pathname === '/api/get') return await handleGet(req, res, url);
    return json(res, 404, {error: 'not found'});
  } catch (error) { return json(res, 500, {error: error?.message || String(error)}); }
});
server.listen(PORT, HOST, () => console.log(`llm-wiki QMD search web listening on http://${HOST}:${PORT} maxResults=${MAX_RESULTS}`));
