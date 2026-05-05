#!/usr/bin/env node
import http from 'node:http';
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { access, readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const HOST = process.env.LLM_WIKI_SEARCH_HOST || '127.0.0.1';
const PORT = Number(process.env.LLM_WIKI_SEARCH_PORT || 8765);
const QMD = process.env.QMD_BIN || 'qmd';
const ROOT = process.env.LLM_WIKI_ROOT || process.cwd();
const MAX_QUERY_CHARS = Number(process.env.LLM_WIKI_SEARCH_MAX_QUERY_CHARS || 1000);
const MAX_RESULTS = Number(process.env.LLM_WIKI_SEARCH_MAX_RESULTS || 500);
const FACET_CACHE_MS = Number(process.env.LLM_WIKI_FACET_CACHE_MS || 300000);
const SEARCH_CACHE_MS = Number(process.env.LLM_WIKI_SEARCH_CACHE_MS || 300000);
const SEARCH_CACHE_MAX = Number(process.env.LLM_WIKI_SEARCH_CACHE_MAX || 100);
const DEFAULT_COLLECTION = process.env.LLM_WIKI_SEARCH_DEFAULT_COLLECTION || 'chunks';
const HYBRID_CANDIDATE_LIMIT = Number(process.env.LLM_WIKI_HYBRID_CANDIDATE_LIMIT || 10);
const SLACK_RUN_ID = process.env.SLACK_RUN_ID || process.env.LLM_WIKI_SLACK_RUN_ID || detectSlackRunId();

const COLLECTIONS = {
  raw: ['slack-raw'],
  slack: ['slack-raw'],
  chunks: ['slack-api-chunks'],
  wiki: ['llm-wiki'],
  all: ['slack-api-chunks', 'slack-raw', 'llm-wiki'],
};
const LOCAL_ROOTS = {
  raw: ['raw/slack'],
  slack: ['raw/slack'],
  chunks: ['qmd/slack-api-chunks'],
  wiki: ['wiki'],
  all: ['qmd/slack-api-chunks', 'raw/slack', 'wiki', 'docs'],
};
const DECORATOR_COLLECTIONS = {raw: 'raw', slack: 'raw', chunks: 'chunks', wiki: 'wiki', all: 'all'};

let facetCache = {expires: 0, data: null};
const searchCache = new Map();
const inFlightSearches = new Map();

function detectSlackRunId() {
  try {
    const state = JSON.parse(readFileSync(path.join(ROOT, '.state/slack-chunk-download-state.json'), 'utf8'));
    if (state?.run_id) return String(state.run_id);
  } catch {
    // Portable public checkouts usually have no local Slack state.
  }
  return 'all-feeds';
}
function json(res, status, body) {
  res.writeHead(status, {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'});
  res.end(JSON.stringify(body));
}
function text(res, status, body, contentType = 'text/plain; charset=utf-8') {
  res.writeHead(status, {'content-type': contentType, 'cache-control': 'no-store'});
  res.end(body);
}
function stripQuotes(value) { return String(value || '').trim().replace(/^['"]|['"]$/g, ''); }
function normalizeChannel(value) { return stripQuotes(value).replace(/^#/, '').trim(); }
function normalizeName(value) { return String(value || '').trim().replace(/^#/, '').toLowerCase(); }
function safeError(error) { return error?.message || String(error); }
function firstMeaningfulLine(value) {
  return String(value || '').split(/\r?\n/).map((line) => line.trim()).find(Boolean) || '';
}
function semanticIndexWarning(stderr) {
  const text = String(stderr || '');
  if (/documents .*need embeddings/i.test(text)) return firstMeaningfulLine(text);
  if (/Could NOT find Vulkan|no GPU acceleration|Failed to build llama\.cpp/i.test(text)) return 'QMD semantic mode produced local model diagnostics; lexical search is the reliable mode until embeddings/model setup is fixed.';
  return '';
}
function parseQmdJson(stdout) {
  const text = String(stdout || '').trim();
  if (!text) return [];
  try { return JSON.parse(text); } catch {}
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (char !== '[' && char !== '{') continue;
    try { return JSON.parse(text.slice(index)); } catch {}
  }
  throw new Error('qmd returned non-json output');
}

function parseDecorators(query) {
  const decorators = {};
  const cleanQuery = String(query || '').replace(/(^|\s)(from|user|in|channel|after|before|on|corpus|mode|sort):("[^"]+"|'[^']+'|[^\s]+)/gi, (_m, prefix, key, rawValue) => {
    const value = stripQuotes(rawValue);
    const name = key.toLowerCase();
    if (name === 'from' || name === 'user') decorators.user = value;
    else if (name === 'in' || name === 'channel') decorators.channel = normalizeChannel(value);
    else if (name === 'after') decorators.dateFrom = value;
    else if (name === 'before') decorators.dateTo = value;
    else if (name === 'on') { decorators.dateFrom = value; decorators.dateTo = value; }
    else if (name === 'mode') decorators.mode = value.toLowerCase();
    else if (name === 'corpus') decorators.collection = DECORATOR_COLLECTIONS[value.toLowerCase()] || value.toLowerCase();
    else if (name === 'sort') decorators.sort = ({newest: 'date-desc', oldest: 'date-asc', date: 'date-desc'}[value.toLowerCase()] || value.toLowerCase());
    return prefix || ' ';
  }).replace(/\s+/g, ' ').trim();
  return {cleanQuery, decorators};
}
function effectiveParams(params) {
  const {cleanQuery, decorators} = parseDecorators(params.get('q') || '');
  const effective = new URLSearchParams(params);
  effective.set('q', cleanQuery);
  for (const [key, value] of Object.entries(decorators)) {
    if (value && !String(effective.get(key) || '').trim()) effective.set(key, value);
  }
  return {effective, decorators, cleanQuery};
}
function requestedCollections(value) {
  const requested = String(value || DEFAULT_COLLECTION).split(',').map((item) => item.trim()).filter(Boolean);
  const qmdCollections = [];
  const localRoots = [];
  for (const item of requested.length ? requested : [DEFAULT_COLLECTION]) {
    if (!COLLECTIONS[item]) throw new Error(`unknown collection: ${item}`);
    qmdCollections.push(...COLLECTIONS[item]);
    localRoots.push(...LOCAL_ROOTS[item]);
  }
  return {qmdCollections: [...new Set(qmdCollections)], localRoots: [...new Set(localRoots)]};
}
function runQmd(args, timeoutMs = 60000) {
  return new Promise((resolve) => {
    const child = spawn(QMD, args, {stdio: ['ignore', 'pipe', 'pipe']});
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      setTimeout(() => child.kill('SIGKILL'), 2000).unref();
    }, timeoutMs);
    child.stdout.on('data', (data) => { stdout += data.toString(); });
    child.stderr.on('data', (data) => { stderr += data.toString(); });
    child.on('close', (code, signal) => { clearTimeout(timer); resolve({code, signal, stdout, stderr}); });
    child.on('error', (error) => { clearTimeout(timer); resolve({code: 127, signal: null, stdout, stderr: safeError(error)}); });
  });
}
function buildSearch(params) {
  const {effective, decorators, cleanQuery} = effectiveParams(params);
  let query = cleanQuery || stripQuotes(effective.get('user') || '') || normalizeChannel(effective.get('channel') || '') || 'wiki';
  if (query.length > MAX_QUERY_CHARS) throw new Error(`query too long; max ${MAX_QUERY_CHARS} chars`);
  const mode = String(effective.get('mode') || 'vec').toLowerCase();
  const limit = Math.min(Math.max(Number(effective.get('n') || 25), 1), MAX_RESULTS);
  const collectionInfo = requestedCollections(effective.get('collection'));
  const user = stripQuotes(effective.get('user') || '');
  if (mode === 'lex' && user && !query.toLowerCase().includes(user.toLowerCase())) query = `${query} "${user}"`;

  let args;
  const typedQuery = /^(lex|vec|hyde|intent|expand):/m.test(query);
  if (mode === 'lex') args = ['search', query];
  else if (mode === 'vec') args = ['query', typedQuery ? query : `vec: ${query}`, '--no-rerank', '-C', String(HYBRID_CANDIDATE_LIMIT)];
  else if (mode === 'hybrid') args = ['query', typedQuery ? query : `lex: ${query}
vec: ${query}`, '--no-rerank', '-C', String(HYBRID_CANDIDATE_LIMIT)];
  else throw new Error(`unknown mode: ${mode}`);
  args.push('-n', String(limit), '--json', '--line-numbers');
  for (const collection of collectionInfo.qmdCollections) args.push('-c', collection);
  return {args, effective, decorators, cleanQuery: query, limit, mode, ...collectionInfo};
}
function parseMeta(result) {
  const file = String(result.file || result.path || '');
  const title = String(result.title || '');
  const meta = {corpus: 'unknown', date: '', channel: '', path: file};
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
  } else if (file.startsWith('qmd://local/')) {
    const rel = file.replace('qmd://local/', '');
    meta.corpus = rel.startsWith('docs/') ? 'docs' : rel.split('/')[0] || 'local';
  }
  const titleMatch = title.match(/#([^\s]+) on (\d{4}-\d{2}-\d{2})/);
  if (titleMatch) { meta.channel ||= titleMatch[1]; meta.date ||= titleMatch[2]; }
  return meta;
}
function enrich(results) { return results.map((result) => ({...result, meta: parseMeta(result)})); }
function applyFilters(results, params) {
  const channel = normalizeChannel(params.get('channel') || '').toLowerCase();
  const user = stripQuotes(params.get('user') || '').toLowerCase();
  const dateFrom = String(params.get('dateFrom') || '').trim();
  const dateTo = String(params.get('dateTo') || '').trim();
  const within = String(params.get('within') || '').trim().toLowerCase();
  return results.filter((result) => {
    const date = result.meta?.date || '';
    const haystack = `${result.title || ''}\n${result.file || ''}\n${result.snippet || ''}\n${result.text || ''}\n${result.context || ''}\n${result.meta?.channel || ''}`.toLowerCase();
    if (channel && !String(result.meta?.channel || '').toLowerCase().includes(channel) && !haystack.includes(channel)) return false;
    if (user && !haystack.includes(user)) return false;
    if (dateFrom && (!date || date < dateFrom)) return false;
    if (dateTo && (!date || date > dateTo)) return false;
    if (within && !haystack.includes(within)) return false;
    return true;
  });
}
function sortResults(results, sort) {
  const copy = [...results];
  if (sort === 'date-desc') copy.sort((a, b) => String(b.meta?.date || '').localeCompare(String(a.meta?.date || '')) || Number(b.score || 0) - Number(a.score || 0));
  else if (sort === 'date-asc') copy.sort((a, b) => String(a.meta?.date || '').localeCompare(String(b.meta?.date || '')) || Number(b.score || 0) - Number(a.score || 0));
  else if (sort === 'channel') copy.sort((a, b) => String(a.meta?.channel || '').localeCompare(String(b.meta?.channel || '')) || String(a.file || '').localeCompare(String(b.file || '')));
  else if (sort === 'corpus') copy.sort((a, b) => String(a.meta?.corpus || '').localeCompare(String(b.meta?.corpus || '')) || String(a.file || '').localeCompare(String(b.file || '')));
  return copy;
}
async function listMarkdownFiles(dir, out = []) {
  let entries = [];
  try { entries = await readdir(dir, {withFileTypes: true}); } catch { return out; }
  for (const entry of entries) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) await listMarkdownFiles(file, out);
    else if (entry.isFile() && entry.name.endsWith('.md')) out.push(file);
  }
  return out;
}
function qmdUriForLocalMarkdown(file) {
  const rel = path.relative(ROOT, file).split(path.sep).join('/');
  if (rel.startsWith('wiki/')) return `qmd://llm-wiki/${rel.slice('wiki/'.length)}`;
  if (rel.startsWith('qmd/slack-api-chunks/')) return `qmd://slack-api-chunks/${rel.slice('qmd/slack-api-chunks/'.length)}`;
  if (rel.startsWith('raw/slack/')) return `qmd://slack-raw/${rel.slice('raw/slack/'.length)}`;
  return `qmd://local/${rel}`;
}
function localPathFromQmdUri(target) {
  const value = String(target || '');
  if (value.startsWith('qmd://llm-wiki/')) return path.join(ROOT, 'wiki', value.slice('qmd://llm-wiki/'.length));
  if (value.startsWith('qmd://slack-api-chunks/')) return path.join(ROOT, 'qmd/slack-api-chunks', value.slice('qmd://slack-api-chunks/'.length));
  if (value.startsWith('qmd://slack-raw/')) return path.join(ROOT, 'raw/slack', value.slice('qmd://slack-raw/'.length));
  if (value.startsWith('qmd://local/')) return path.join(ROOT, value.slice('qmd://local/'.length));
  return '';
}
function plainTerms(query) {
  return String(query || '')
    .replace(/(^|\s)(from|user|in|channel|after|before|on|corpus|mode|sort):("[^"]+"|'[^']+'|[^\s]+)/gi, ' ')
    .replace(/^(lex|vec|hyde|intent|expand):/gmi, ' ')
    .split(/\s+/)
    .map((term) => term.trim().replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '').toLowerCase())
    .filter((term) => term.length >= 2);
}
function titleFromMarkdown(file, body) {
  return body.match(/^#\s+(.+)$/m)?.[1]?.trim() || path.basename(file, '.md').replace(/[-_]/g, ' ');
}
function snippetForTerms(body, terms) {
  const lines = String(body || '').split(/\r?\n/).filter((line) => line.trim());
  const index = lines.findIndex((line) => terms.some((term) => line.toLowerCase().includes(term)));
  const start = Math.max(0, index < 0 ? 0 : index - 2);
  return lines.slice(start, start + 8).join('\n').slice(0, 1600);
}
async function existingLocalRoots(names) {
  const roots = [];
  for (const name of names) {
    const root = path.join(ROOT, name);
    try { await access(root); roots.push(root); } catch {}
  }
  return roots;
}
async function localMarkdownSearch(built, startedAt, warning) {
  const terms = plainTerms(built.cleanQuery || built.effective.get('q') || '');
  const roots = await existingLocalRoots(built.localRoots);
  const files = [];
  for (const root of roots) await listMarkdownFiles(root, files);
  const results = [];
  for (const file of files) {
    let body = '';
    try { body = await readFile(file, 'utf8'); } catch { continue; }
    const haystack = `${path.relative(ROOT, file)}\n${body}`.toLowerCase();
    const hits = terms.length ? terms.reduce((sum, term) => sum + (haystack.includes(term) ? 1 : 0), 0) : 1;
    if (!hits && terms.length) continue;
    results.push({file: qmdUriForLocalMarkdown(file), title: titleFromMarkdown(file, body), score: terms.length ? hits / terms.length : 0.1, snippet: snippetForTerms(body, terms), backend: 'local-markdown-fallback'});
  }
  return finishSearch(results, built, startedAt, warning || 'QMD unavailable; searched local markdown files instead.', 'local-markdown-fallback');
}
async function finishSearch(results, built, startedAt, warning = '', backend = 'qmd') {
  const enriched = enrich(results);
  const filtered = applyFilters(enriched, built.effective);
  const sorted = sortResults(filtered, String(built.effective.get('sort') || 'relevance'));
  const limited = sorted.slice(0, built.limit);
  return {backend, warning, args: built.args, decorators: built.decorators, effectiveQuery: built.cleanQuery, maxResults: MAX_RESULTS, fetched: results.length, matched: filtered.length, returned: limited.length, elapsedMs: Date.now() - startedAt, results: limited};
}
async function qmdLexicalFallback(built, startedAt, reason) {
  const query = built.cleanQuery || stripQuotes(built.effective.get('user') || '') || normalizeChannel(built.effective.get('channel') || '') || 'wiki';
  const args = ['search', query, '-n', String(built.limit), '--json', '--line-numbers'];
  for (const collection of built.qmdCollections) args.push('-c', collection);
  const result = await runQmd(args, 60000);
  if (result.code !== 0) return localMarkdownSearch({...built, args, mode: 'lex'}, startedAt, `${reason}; QMD lexical fallback failed (${result.code}), searched local markdown files instead.`);
  try {
    return finishSearch(parseQmdJson(result.stdout), {...built, args, mode: 'lex'}, startedAt, `${reason}; fell back to QMD lexical search.`, 'qmd');
  } catch {
    return localMarkdownSearch({...built, args, mode: 'lex'}, startedAt, `${reason}; QMD lexical fallback returned unusable output, searched local markdown files instead.`);
  }
}
async function qmdSearchOrFallback(built, startedAt) {
  const result = await runQmd(built.args, built.mode === 'hybrid' || built.mode === 'vec' ? 90000 : 60000);
  if (result.code !== 0) {
    if (built.mode !== 'lex') return qmdLexicalFallback(built, startedAt, `QMD ${built.mode} search failed (${result.code})`);
    return localMarkdownSearch(built, startedAt, `QMD lexical search failed (${result.code}); searched local markdown files instead.`);
  }
  try {
    const warning = built.mode === 'lex' ? '' : semanticIndexWarning(result.stderr);
    return finishSearch(parseQmdJson(result.stdout), built, startedAt, warning, 'qmd');
  } catch {
    if (built.mode !== 'lex') return qmdLexicalFallback(built, startedAt, `QMD ${built.mode} search returned unusable output`);
    return localMarkdownSearch(built, startedAt, 'QMD lexical search returned unusable output; searched local markdown files instead.');
  }
}
function cloneJson(value) { return JSON.parse(JSON.stringify(value)); }
function cacheKeyForSearch(built) {
  return JSON.stringify({
    args: built.args,
    effective: [...built.effective.entries()].sort(([a], [b]) => a.localeCompare(b)),
    qmdCollections: built.qmdCollections,
    localRoots: built.localRoots,
    limit: built.limit,
    mode: built.mode,
  });
}
function getCachedSearch(key) {
  if (!SEARCH_CACHE_MS) return null;
  const entry = searchCache.get(key);
  if (!entry) return null;
  if (entry.expires <= Date.now()) { searchCache.delete(key); return null; }
  searchCache.delete(key);
  searchCache.set(key, entry);
  return cloneJson(entry.data);
}
function setCachedSearch(key, data) {
  if (!SEARCH_CACHE_MS) return;
  searchCache.set(key, {expires: Date.now() + SEARCH_CACHE_MS, data: cloneJson(data)});
  while (searchCache.size > SEARCH_CACHE_MAX) searchCache.delete(searchCache.keys().next().value);
}
async function cachedSearch(built) {
  const key = cacheKeyForSearch(built);
  const cached = getCachedSearch(key);
  if (cached) return {...cached, cacheHit: true};
  if (inFlightSearches.has(key)) return {...cloneJson(await inFlightSearches.get(key)), cacheHit: 'shared'};
  const startedAt = Date.now();
  const promise = qmdSearchOrFallback(built, startedAt);
  inFlightSearches.set(key, promise);
  try {
    const data = await promise;
    setCachedSearch(key, data);
    return data;
  } finally {
    inFlightSearches.delete(key);
  }
}
async function handleSearch(_req, res, url) {
  const startedAt = Date.now();
  let built;
  try { built = buildSearch(url.searchParams); }
  catch (error) { return json(res, 400, {error: safeError(error)}); }
  const data = await cachedSearch(built);
  if (data.cacheHit) data.elapsedMs = Date.now() - startedAt;
  return json(res, 200, data);
}
async function handleGet(_req, res, url) {
  const target = String(url.searchParams.get('target') || '').trim();
  const lines = Math.min(Math.max(Number(url.searchParams.get('lines') || 160), 1), 1000);
  if (!target.startsWith('qmd://') && !target.startsWith('#')) return json(res, 400, {error: 'target must be a qmd:// path or #docid'});
  const result = await runQmd(['get', target, '-l', String(lines)], 60000);
  if (result.code === 0) return text(res, 200, result.stdout);
  const local = localPathFromQmdUri(target);
  if (local) {
    try { return text(res, 200, (await readFile(local, 'utf8')).split(/\r?\n/).slice(0, lines).join('\n')); }
    catch {}
  }
  return json(res, 500, {error: 'document not available', stderr: result.stderr.slice(-1000)});
}
async function readJsonIfExists(file, fallback) {
  try { return JSON.parse(await readFile(file, 'utf8')); } catch { return fallback; }
}
async function buildFacets() {
  const now = Date.now();
  if (facetCache.data && facetCache.expires > now) return facetCache.data;
  const channels = new Map();
  const users = new Map();
  const conversations = await readJsonIfExists(path.join(ROOT, 'chunks/slack', SLACK_RUN_ID, 'conversations.json'), []);
  for (const conversation of Array.isArray(conversations) ? conversations : []) {
    const name = conversation.name || conversation.name_normalized || conversation.id;
    if (name) channels.set(name, {name, id: conversation.id || '', count: 0});
  }
  const profiles = await readJsonIfExists(path.join(ROOT, 'chunks/slack', SLACK_RUN_ID, 'user_profiles.json'), {});
  for (const [id, profile] of Object.entries(profiles || {})) {
    const name = profile.display_name || profile.real_name || profile.name || id;
    users.set(name, {name, id, realName: profile.real_name || '', slackName: profile.name || '', count: 0});
  }
  for (const file of await listMarkdownFiles(path.join(ROOT, 'raw/slack'))) {
    const rel = path.relative(path.join(ROOT, 'raw/slack'), file);
    const parts = rel.split(path.sep);
    if (parts[1]) {
      const name = parts[1].replace(/\.md$/, '');
      const current = channels.get(name) || {name, id: '', count: 0};
      current.count += 1;
      channels.set(name, current);
    }
    try {
      const body = await readFile(file, 'utf8');
      for (const match of body.matchAll(/^##\s+[^|\n]+\|\s+([^|\n]+?)\s+\|\s+ts=/gm)) {
        const name = match[1].trim();
        const current = users.get(name) || {name, id: '', count: 0};
        current.count += 1;
        users.set(name, current);
      }
    } catch {}
  }
  const data = {
    channels: [...channels.values()].sort((a, b) => (b.count || 0) - (a.count || 0) || a.name.localeCompare(b.name)).slice(0, 5000),
    users: [...users.values()].sort((a, b) => (b.count || 0) - (a.count || 0) || a.name.localeCompare(b.name)).slice(0, 5000),
  };
  facetCache = {expires: now + FACET_CACHE_MS, data};
  return data;
}

const htmlPath = path.join(path.dirname(fileURLToPath(import.meta.url)), 'qmd-search.html');
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://${req.headers.host || `${HOST}:${PORT}`}`);
  try {
    if (url.pathname === '/' || url.pathname === '/index.html') return text(res, 200, await readFile(htmlPath, 'utf8'), 'text/html; charset=utf-8');
    if (url.pathname === '/favicon.ico') { res.writeHead(204, {'cache-control': 'public, max-age=86400'}); return res.end(); }
    if (url.pathname === '/health') return json(res, 200, {ok: true, service: 'qmd-search', qmd: QMD, root: ROOT, maxResults: MAX_RESULTS, defaultCollection: DEFAULT_COLLECTION, searchCacheMs: SEARCH_CACHE_MS, searchCacheSize: searchCache.size, hybridCandidateLimit: HYBRID_CANDIDATE_LIMIT});
    if (url.pathname === '/api/facets') return json(res, 200, await buildFacets());
    if (url.pathname === '/api/search') return handleSearch(req, res, url);
    if (url.pathname === '/api/get') return handleGet(req, res, url);
    return json(res, 404, {error: 'not found'});
  } catch (error) {
    return json(res, 500, {error: safeError(error)});
  }
});
server.listen(PORT, HOST, () => console.log(`QMD search listening on http://${HOST}:${PORT} maxResults=${MAX_RESULTS}`));
