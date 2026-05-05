#!/usr/bin/env node
import http from 'node:http';
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { access, readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const HOST = process.env.LLM_WIKI_SEARCH_HOST || '127.0.0.1';
const PORT = Number(process.env.LLM_WIKI_SEARCH_PORT || 8765);
const QMD = process.env.QMD_BIN || 'qmd';
const ROOT = process.env.LLM_WIKI_ROOT || process.cwd();
// The UI can run from a feature worktree while the large local Slack/QMD corpus
// stays in the canonical workspace. Search uses qmd's index; facets/local
// fallbacks need an on-disk corpus root to scan.
const DATA_ROOT = process.env.LLM_WIKI_DATA_ROOT || ROOT;
const MAX_QUERY_CHARS = Number(process.env.LLM_WIKI_SEARCH_MAX_QUERY_CHARS || 1000);
const MAX_RESULTS = Number(process.env.LLM_WIKI_SEARCH_MAX_RESULTS || 500);
const FACET_CACHE_MS = Number(process.env.LLM_WIKI_FACET_CACHE_MS || 300000);
const SEARCH_CACHE_MS = Number(process.env.LLM_WIKI_SEARCH_CACHE_MS || 300000);
const SEARCH_CACHE_MAX = Number(process.env.LLM_WIKI_SEARCH_CACHE_MAX || 100);
const DEFAULT_COLLECTION = process.env.LLM_WIKI_SEARCH_DEFAULT_COLLECTION || 'chunks';
const HYBRID_CANDIDATE_LIMIT = Number(process.env.LLM_WIKI_HYBRID_CANDIDATE_LIMIT || 10);
const SLACK_RUN_ID = process.env.SLACK_RUN_ID || process.env.LLM_WIKI_SLACK_RUN_ID || detectSlackRunId();
const SLACK_WORKSPACE_URL = (process.env.SLACK_WORKSPACE_URL || 'https://displayr.slack.com').replace(/\/$/, '');

const COLLECTIONS = {
  raw: ['slack-raw'],
  slack: ['slack-raw'],
  chunks: ['slack-api-chunks'],
  conversation: ['slack-conversations'],
  conversations: ['slack-conversations'],
  batches: ['slack-conversations'],
  wiki: ['llm-wiki'],
  transcripts: ['huddle-transcripts'],
  huddles: ['huddle-transcripts'],
  standups: ['huddle-transcripts'],
  meetings: ['huddle-transcripts'],
  all: ['slack-api-chunks', 'slack-conversations', 'slack-raw', 'llm-wiki', 'huddle-transcripts'],
};
const LOCAL_ROOTS = {
  raw: ['raw/slack'],
  slack: ['raw/slack'],
  chunks: ['qmd/slack-api-chunks'],
  conversation: ['qmd/slack-conversations'],
  conversations: ['qmd/slack-conversations'],
  batches: ['qmd/slack-conversations'],
  wiki: ['wiki'],
  transcripts: ['qmd/huddle-transcripts', 'docs/huddle-transcripts', 'docs/standup-transcripts'],
  huddles: ['qmd/huddle-transcripts', 'docs/huddle-transcripts'],
  standups: ['qmd/huddle-transcripts', 'docs/standup-transcripts'],
  meetings: ['qmd/huddle-transcripts', 'docs/huddle-transcripts', 'docs/standup-transcripts'],
  all: ['qmd/slack-api-chunks', 'qmd/slack-conversations', 'raw/slack', 'wiki', 'docs', 'qmd/huddle-transcripts'],
};
const DECORATOR_COLLECTIONS = {raw: 'raw', slack: 'raw', chunks: 'chunks', conversation: 'conversations', conversations: 'conversations', batches: 'conversations', wiki: 'wiki', transcripts: 'transcripts', transcript: 'transcripts', huddle: 'transcripts', huddles: 'transcripts', standup: 'transcripts', standups: 'transcripts', meetings: 'transcripts', all: 'all'};

let facetCache = {expires: 0, data: null};
let userProfileAliasCache = null;
let channelAliasCache = null;
const searchCache = new Map();
const inFlightSearches = new Map();

function detectSlackRunId() {
  try {
    const state = JSON.parse(readFileSync(path.join(DATA_ROOT, '.state/slack-chunk-download-state.json'), 'utf8'));
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
function escapeRegex(value) { return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
function isoDate(date) { return date.toISOString().slice(0, 10); }
function parseIsoDay(value) {
  const match = String(value || '').trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return null;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return Number.isNaN(date.getTime()) ? null : date;
}
function relativeDateFrom(value, now = new Date()) {
  const key = String(value || '').trim().toLowerCase();
  if (!key) return '';
  const date = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  if (key === 'last-week') date.setUTCDate(date.getUTCDate() - 7);
  else if (key === 'last-month') date.setUTCMonth(date.getUTCMonth() - 1);
  else if (key === 'last-year') date.setUTCFullYear(date.getUTCFullYear() - 1);
  else return '';
  return isoDate(date);
}
function slackTsToPath(ts) { return String(ts || '').replace(/[^0-9]/g, ''); }
function slackPathToTs(value) {
  const text = String(value || '');
  const match = text.match(/(\d{10})[-.](\d{6})/);
  return match ? `${match[1]}.${match[2]}` : '';
}
function slackTsDate(value) {
  const seconds = Number(String(value || '').split('.')[0]);
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  return isoDate(new Date(seconds * 1000));
}
function slackMessageUrl(channelId, ts, threadTs = '') {
  const channel = String(channelId || '').toUpperCase();
  const messageTs = String(ts || '').trim();
  if (!channel || !messageTs) return '';
  const base = `${SLACK_WORKSPACE_URL}/archives/${encodeURIComponent(channel)}/p${slackTsToPath(messageTs)}`;
  const thread = String(threadTs || '').trim();
  return thread && thread !== messageTs ? `${base}?thread_ts=${encodeURIComponent(thread)}&cid=${encodeURIComponent(channel)}` : base;
}
function slackFileUrl(fileId) {
  const id = String(fileId || '').trim();
  return id ? `${SLACK_WORKSPACE_URL}/files/${encodeURIComponent(id)}` : '';
}
function frontmatterField(text, key) {
  const match = String(text || '').match(new RegExp(`^${key}:\\s*\"?([^\\n\"]+)\"?`, 'm'));
  return match ? match[1].trim() : '';
}
function sourceTextForUri(uri) {
  try {
    const local = localPathFromQmdUri(uri);
    if (local && existsSync(local)) return readFileSync(local, 'utf8');
    const value = String(uri || '');
    if (value.startsWith('qmd://huddle-transcripts/')) {
      const rel = value.slice('qmd://huddle-transcripts/'.length);
      const dir = path.dirname(path.join(DATA_ROOT, 'qmd/huddle-transcripts', rel));
      const base = path.basename(rel).replace(/^(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})-/, '$1_$2-$3_');
      for (const candidate of readdirSync(dir)) {
        if (candidate === base || candidate.replace(/_/g, '-') === path.basename(rel)) return readFileSync(path.join(dir, candidate), 'utf8');
      }
    }
    return '';
  } catch {
    return '';
  }
}
function firstSlackHeader(text) {
  const normalized = String(text || '').split(/\r?\n/).map((line) => line.replace(/^\s*\d+:\s?/, '')).join('\n');
  const match = normalized.match(/^##\s+(\d{4}-\d{2}-\d{2})T[^|]+\|\s+([^|]+?)\s+\|\s+ts=(\d+\.\d+)\s+thread_ts=([^\s]*)/m);
  return match ? {date: match[1], speaker: match[2].trim(), ts: match[3], threadTs: match[4] || ''} : {};
}
function slackHeaders(text) {
  const normalized = String(text || '').split(/\r?\n/).map((line) => line.replace(/^\s*\d+:\s?/, '')).join('\n');
  return [...normalized.matchAll(/^##\s+(\d{4}-\d{2}-\d{2})T[^|]+\|\s+([^|]+?)\s+\|\s+ts=(\d+\.\d+)\s+thread_ts=([^\s]*)/gm)]
    .map((match) => ({date: match[1], speaker: match[2].trim(), ts: match[3], threadTs: match[4] || ''}));
}
function normalizeUserAlias(value) { return stripQuotes(value).replace(/^@/, '').trim().toLowerCase(); }
function userProfileAliases() {
  if (userProfileAliasCache) return userProfileAliasCache;
  const aliases = new Map();
  try {
    const profiles = JSON.parse(readFileSync(path.join(DATA_ROOT, 'chunks/slack', SLACK_RUN_ID, 'user_profiles.json'), 'utf8'));
    for (const [id, profile] of Object.entries(profiles || {})) {
      const values = [id, profile?.display_name, profile?.real_name, profile?.name].map(normalizeUserAlias).filter(Boolean);
      for (const value of values) aliases.set(value, new Set(values));
    }
  } catch {
    // Portable checkouts usually do not include private Slack profile metadata.
  }
  userProfileAliasCache = aliases;
  return aliases;
}
function aliasesForUserFilter(value) {
  const normalized = normalizeUserAlias(value);
  if (!normalized) return new Set();
  return new Set([normalized, ...(userProfileAliases().get(normalized) || [])]);
}
function channelAliases() {
  if (channelAliasCache) return channelAliasCache;
  const aliases = new Map();
  try {
    const conversations = JSON.parse(readFileSync(path.join(DATA_ROOT, 'chunks/slack', SLACK_RUN_ID, 'conversations.json'), 'utf8'));
    for (const conversation of Array.isArray(conversations) ? conversations : []) {
      const values = [conversation.id, conversation.name, conversation.name_normalized]
        .map((value) => normalizeName(value || ''))
        .filter(Boolean);
      for (const value of values) aliases.set(value, new Set(values));
    }
  } catch {
    // Portable checkouts usually do not include private Slack conversation metadata.
  }
  channelAliasCache = aliases;
  return aliases;
}
function aliasesForChannelFilter(value) {
  const normalized = normalizeName(value);
  if (!normalized) return new Set();
  return new Set([normalized, ...(channelAliases().get(normalized) || [])]);
}
function slackMessageBlocks(text) {
  const lines = String(text || '').split(/\r?\n/).map((line) => line.replace(/^\s*\d+:\s?/, '').trimEnd());
  const blocks = [];
  let current = null;
  const headerPattern = /^##\s+(\d{4}-\d{2}-\d{2})T[^|]+\|\s+([^|]+?)\s+\|\s+ts=(\d+\.\d+)\s+thread_ts=([^\s]*)/;
  for (const line of lines) {
    const header = line.match(headerPattern);
    if (header) {
      if (current) blocks.push(current);
      current = {header: line, date: header[1], speaker: header[2].trim(), ts: header[3], threadTs: header[4] || '', body: []};
      continue;
    }
    if (!current) continue;
    if (/^@@|^---$|^#\s+Slack chunk|^Source JSON:|^(source|kind|channel_id|channel_name|thread_ts|page|fetched_at|message_count|source_json):/i.test(line.trim())) continue;
    current.body.push(line);
  }
  if (current) blocks.push(current);
  return blocks.filter((block) => block.body.join('\n').trim());
}
function slackSpeakersForResult(result) {
  const speakers = new Set();
  if (result?.meta?.user) speakers.add(stripQuotes(result.meta.user));
  for (const source of [result?.snippet, result?.text, result?.context]) {
    for (const header of slackHeaders(source)) speakers.add(header.speaker);
  }
  const file = String(result?.file || result?.path || '');
  if (/^qmd:\/\/slack-(raw|api-chunks|conversations)\//.test(file)) {
    for (const header of slackHeaders(sourceTextForUri(file))) speakers.add(header.speaker);
  }
  return [...speakers].filter(Boolean);
}
function isSlackResult(result) {
  const kind = String(result?.meta?.kind || '').toLowerCase();
  const file = String(result?.file || result?.path || '');
  return ['raw', 'chunks', 'conversations'].includes(kind) || /^qmd:\/\/slack-(raw|api-chunks|conversations)\//.test(file);
}
function matchesSlackSender(result, userValue) {
  const aliases = aliasesForUserFilter(userValue);
  if (!aliases.size) return true;
  if (!isSlackResult(result)) return false;
  return slackSpeakersForResult(result).some((speaker) => aliases.has(normalizeUserAlias(speaker)));
}
function focusResultOnSlackSender(result, userValue) {
  const aliases = aliasesForUserFilter(userValue);
  if (!aliases.size || !isSlackResult(result)) return result;
  const file = String(result?.file || result?.path || '');
  const sources = [result?.snippet, result?.text, result?.context];
  const sourceText = sourceTextForUri(file);
  if (sourceText) sources.push(sourceText);
  const seen = new Set();
  const matches = [];
  for (const source of sources) {
    for (const block of slackMessageBlocks(source)) {
      const key = `${block.ts}:${block.speaker}:${block.body.join('\n')}`;
      if (seen.has(key)) continue;
      seen.add(key);
      if (aliases.has(normalizeUserAlias(block.speaker))) matches.push(block);
    }
  }
  if (!matches.length) return result;
  const snippet = matches.slice(0, 12).map((block) => `${block.header}\n${block.body.join('\n').trim()}`).join('\n\n');
  const focused = {...result, snippet, text: '', context: '', matchedSenderMessages: matches.length};
  focused.meta = parseMeta(focused);
  return focused;
}
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
  const relativeFrom = relativeDateFrom(effective.get('relative') || effective.get('time'));
  if (relativeFrom && !String(effective.get('dateFrom') || '').trim()) effective.set('dateFrom', relativeFrom);
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
  const fieldOnlyQuery = !cleanQuery && Boolean(user || normalizeChannel(effective.get('channel') || '') || effective.get('dateFrom') || effective.get('dateTo'));
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
  return {args, effective, decorators, cleanQuery: query, limit, mode, fieldOnlyQuery, ...collectionInfo};
}
function prettifySlug(value) {
  return String(value || '')
    .replace(/\.md$/, '')
    .replace(/^huddle-transcripts-/, '')
    .replace(/^standup-transcripts-/, '')
    .replace(/-/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}
function parseTranscriptMeta(file, title) {
  const rel = file.replace('qmd://huddle-transcripts/', '');
  const parts = rel.split('/');
  const folder = parts[1] || parts[0] || '';
  const basename = parts.at(-1) || '';
  const sourceText = sourceTextForUri(file);
  const dateMatch = basename.match(/(\d{4}-\d{2}-\d{2})(?:-(\d{2})-(\d{2}))?/);
  const kind = folder.startsWith('standup-transcripts-') || /standup/i.test(basename) ? 'standup' : 'huddle';
  const participant = folder ? prettifySlug(folder) : '';
  const canvasId = frontmatterField(sourceText, 'canvas');
  const transcriptFileId = frontmatterField(sourceText, 'transcript_file');
  const links = [];
  const canvasUrl = slackFileUrl(canvasId);
  const transcriptUrl = slackFileUrl(transcriptFileId);
  if (canvasUrl) links.push({label: kind === 'standup' ? 'Open standup notes' : 'Open huddle notes', url: canvasUrl});
  if (transcriptUrl && transcriptUrl !== canvasUrl) links.push({label: 'Open transcript file', url: transcriptUrl});
  return {
    corpus: 'Transcripts',
    kind: 'transcripts',
    date: dateMatch ? dateMatch[1] : '',
    channel: kind === 'standup' ? participant : '',
    path: file,
    transcript: true,
    transcriptKind: kind,
    transcriptWith: kind === 'huddle' ? participant : '',
    transcriptTeam: kind === 'standup' ? participant : '',
    transcriptLabel: title || prettifySlug(basename),
    slackCanvasId: canvasId,
    slackTranscriptFileId: transcriptFileId,
    slackUrl: links[0]?.url || '',
    slackLinks: links,
  };
}
function parseMeta(result) {
  const file = String(result.file || result.path || '');
  const title = String(result.title || '');
  const snippet = String(result.snippet || result.text || '');
  const meta = {corpus: 'unknown', kind: 'unknown', date: '', channel: '', path: file, slackLinks: []};
  if (file.startsWith('qmd://slack-raw/')) {
    meta.corpus = 'raw Slack';
    meta.kind = 'raw';
    const parts = file.replace('qmd://slack-raw/', '').split('/');
    if (/^\d{4}-\d{2}-\d{2}$/.test(parts[0] || '')) meta.date = parts[0];
    if (parts[1]) meta.channel = parts[1].replace(/\.md$/, '');
    const sourceText = sourceTextForUri(file);
    const channelId = frontmatterField(sourceText, 'channel_id') || frontmatterField(snippet, 'channel_id');
    const header = firstSlackHeader(snippet) || firstSlackHeader(sourceText);
    if (channelId) meta.channelId = channelId.toUpperCase();
    if (header.date) meta.date ||= header.date;
    if (header.speaker) meta.user = header.speaker;
    if (header.ts && meta.channelId) {
      meta.slackMessageUrl = slackMessageUrl(meta.channelId, header.ts, header.threadTs);
      meta.slackUrl = meta.slackMessageUrl;
      meta.slackLinks = [{label: 'Open message in Slack', url: meta.slackMessageUrl}];
    }
  } else if (file.startsWith('qmd://slack-api-chunks/')) {
    meta.corpus = 'Slack';
    meta.kind = 'chunks';
    const parts = file.replace('qmd://slack-api-chunks/', '').split('/');
    const titleChannel = title.match(/#([^\s]+)\s+/)?.[1] || '';
    meta.channel = titleChannel || parts[1] || '';
    meta.channelId = String(parts[1] || '').toUpperCase();
    const header = firstSlackHeader(snippet);
    if (header.date) meta.date = header.date;
    if (header.speaker) meta.user = header.speaker;
    const pathThreadTs = parts[2] === 'replies' ? slackPathToTs(parts[3]) : '';
    if (!meta.date && pathThreadTs) meta.date = slackTsDate(pathThreadTs);
    const threadTs = header.threadTs || pathThreadTs || header.ts || '';
    const messageTs = header.ts || threadTs;
    if (meta.channelId && threadTs) meta.slackThreadUrl = slackMessageUrl(meta.channelId, threadTs);
    if (meta.channelId && messageTs) meta.slackMessageUrl = slackMessageUrl(meta.channelId, messageTs, threadTs);
    meta.slackUrl = meta.slackMessageUrl || meta.slackThreadUrl || '';
    if (meta.slackUrl) meta.slackLinks.push({label: 'Open message in Slack', url: meta.slackUrl});
    if (meta.slackThreadUrl && meta.slackThreadUrl !== meta.slackUrl) meta.slackLinks.push({label: 'Open thread in Slack', url: meta.slackThreadUrl});
  } else if (file.startsWith('qmd://slack-conversations/')) {
    meta.corpus = 'Slack conversations';
    meta.kind = 'conversations';
    const parts = file.replace('qmd://slack-conversations/', '').split('/');
    meta.channel = parts[1] || '';
    meta.channelId = String(parts[1] || '').toUpperCase();
    const header = firstSlackHeader(snippet);
    if (header.date) meta.date = header.date;
    if (header.speaker) meta.user = header.speaker;
    const pathThreadTs = slackPathToTs(parts.find((part) => /\d{10}-\d{6}/.test(part)) || '');
    if (!meta.date && pathThreadTs) meta.date = slackTsDate(pathThreadTs);
    const threadTs = header.threadTs || pathThreadTs || header.ts || '';
    const messageTs = header.ts || threadTs;
    if (meta.channelId && threadTs) meta.slackThreadUrl = slackMessageUrl(meta.channelId, threadTs);
    if (meta.channelId && messageTs) meta.slackMessageUrl = slackMessageUrl(meta.channelId, messageTs, threadTs);
    meta.slackUrl = meta.slackMessageUrl || meta.slackThreadUrl || '';
    if (meta.slackUrl) meta.slackLinks.push({label: 'Open message in Slack', url: meta.slackUrl});
    if (meta.slackThreadUrl && meta.slackThreadUrl !== meta.slackUrl) meta.slackLinks.push({label: 'Open thread in Slack', url: meta.slackThreadUrl});
  } else if (file.startsWith('qmd://llm-wiki/')) {
    meta.corpus = 'wiki';
    meta.kind = 'wiki';
    const rel = file.replace('qmd://llm-wiki/', '');
    meta.channel = rel.split('/')[0] || '';
    const sourceText = sourceTextForUri(file);
    const sourceChannel = sourceText.match(/\bchannel=([^\s`]+)/i)?.[1] || '';
    const fileDate = rel.match(/(\d{4}-\d{2}-\d{2})/)?.[1] || sourceText.match(/^updated:\s*(\d{4}-\d{2}-\d{2})/m)?.[1] || '';
    if (sourceChannel) meta.channel = normalizeChannel(sourceChannel);
    if (fileDate) meta.date = fileDate;
  } else if (file.startsWith('qmd://huddle-transcripts/')) {
    Object.assign(meta, parseTranscriptMeta(file, title));
  } else if (file.startsWith('qmd://local/')) {
    const rel = file.replace('qmd://local/', '');
    meta.corpus = rel.startsWith('docs/') ? 'docs' : rel.split('/')[0] || 'local';
    meta.kind = rel.includes('transcript') ? 'transcripts' : (rel.startsWith('docs/') ? 'docs' : 'local');
    if (meta.kind === 'transcripts') meta.corpus = 'Transcripts';
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
    if (user && !matchesSlackSender(result, user)) return false;
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
  const rel = path.relative(DATA_ROOT, file).split(path.sep).join('/');
  if (rel.startsWith('wiki/')) return `qmd://llm-wiki/${rel.slice('wiki/'.length)}`;
  if (rel.startsWith('qmd/slack-api-chunks/')) return `qmd://slack-api-chunks/${rel.slice('qmd/slack-api-chunks/'.length)}`;
  if (rel.startsWith('qmd/slack-conversations/')) return `qmd://slack-conversations/${rel.slice('qmd/slack-conversations/'.length)}`;
  if (rel.startsWith('raw/slack/')) return `qmd://slack-raw/${rel.slice('raw/slack/'.length)}`;
  return `qmd://local/${rel}`;
}
function localPathFromQmdUri(target) {
  const value = String(target || '');
  if (value.startsWith('qmd://llm-wiki/')) return path.join(DATA_ROOT, 'wiki', value.slice('qmd://llm-wiki/'.length));
  if (value.startsWith('qmd://slack-api-chunks/')) return path.join(DATA_ROOT, 'qmd/slack-api-chunks', value.slice('qmd://slack-api-chunks/'.length));
  if (value.startsWith('qmd://slack-conversations/')) return path.join(DATA_ROOT, 'qmd/slack-conversations', value.slice('qmd://slack-conversations/'.length));
  if (value.startsWith('qmd://slack-raw/')) return path.join(DATA_ROOT, 'raw/slack', value.slice('qmd://slack-raw/'.length));
  if (value.startsWith('qmd://huddle-transcripts/')) return path.join(DATA_ROOT, 'qmd/huddle-transcripts', value.slice('qmd://huddle-transcripts/'.length));
  if (value.startsWith('qmd://local/')) return path.join(DATA_ROOT, value.slice('qmd://local/'.length));
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
    const root = path.join(DATA_ROOT, name);
    try { await access(root); roots.push(root); } catch {}
  }
  return roots;
}
function dateValuesForFilter(params) {
  const from = parseIsoDay(params.get('dateFrom') || '');
  const to = parseIsoDay(params.get('dateTo') || '') || (from ? parseIsoDay(isoDate(new Date())) : null);
  if (!from || !to || from > to) return null;
  const values = [];
  const cursor = new Date(from.getTime());
  while (cursor <= to && values.length <= 370) {
    values.push(isoDate(cursor));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return values.length <= 370 ? values : null;
}
function runGrepFiles(pattern, roots, {ignoreCase = false} = {}) {
  if (!pattern || !roots.length) return null;
  const args = ['-R', '-I', '-l', '-E', '--include=*.md'];
  if (ignoreCase) args.push('-i');
  args.push(pattern, ...roots);
  const result = spawnSync('grep', args, {encoding: 'utf8', timeout: 20000, maxBuffer: 50 * 1024 * 1024});
  if (result.error || result.signal || Number(result.status) > 1) return null;
  return String(result.stdout || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
}
function runRipgrepFiles(pattern, roots, options = {}) {
  // Keep the function name for callers, but use POSIX grep because the VM that
  // serves this UI does not currently install ripgrep. This is still much
  // faster than opening every markdown file in Node for field-only searches.
  return runGrepFiles(pattern, roots, options);
}
function intersectFileLists(lists) {
  const usable = lists.filter((list) => Array.isArray(list));
  if (!usable.length) return null;
  const [first, ...rest] = usable.sort((a, b) => a.length - b.length);
  const others = rest.map((list) => new Set(list));
  return first.filter((file) => others.every((set) => set.has(file)));
}
function localFileMatchesChannel(file, channel) {
  const aliases = aliasesForChannelFilter(channel);
  if (!aliases.size) return true;
  const rel = path.relative(DATA_ROOT, file).split(path.sep).join('/').toLowerCase();
  return [...aliases].some((alias) => rel.includes(`/${alias}/`) || rel.includes(`/${alias}.md`) || rel.includes(`-${alias}.md`) || rel.includes(`${alias}/`));
}
async function localFieldCandidateFiles(roots, params) {
  const candidateLists = [];
  const user = stripQuotes(params.get('user') || '');
  const userAliases = [...aliasesForUserFilter(user)].filter(Boolean);
  const speakers = userAliases.length ? userAliases.map(escapeRegex).join('|') : '';
  const dates = dateValuesForFilter(params);
  if (speakers && dates?.length) {
    candidateLists.push(await runRipgrepFiles(`^##[[:space:]]+(${dates.join('|')})T[^|]*\\|[[:space:]]*(${speakers})[[:space:]]*\\|[[:space:]]+ts=`, roots, {ignoreCase: true}));
  } else if (speakers) {
    candidateLists.push(await runRipgrepFiles(`^##[[:space:]]+[^|]+\\|[[:space:]]*(${speakers})[[:space:]]*\\|[[:space:]]+ts=`, roots, {ignoreCase: true}));
  } else if (dates?.length) {
    candidateLists.push(await runRipgrepFiles(`^##[[:space:]]+(${dates.join('|')})T`, roots));
  }
  const nonEmptyCandidateLists = candidateLists.filter((list) => Array.isArray(list) && list.length);
  let files = intersectFileLists(candidateLists);
  if (files?.length === 0 && nonEmptyCandidateLists.length > 1) {
    // If grep-compatible path output differs between field probes, keep the
    // most selective field candidate set and let the normal semantic filters
    // below enforce correctness. This still avoids a full corpus scan.
    files = nonEmptyCandidateLists.sort((a, b) => a.length - b.length)[0];
  }
  const channel = normalizeChannel(params.get('channel') || '');
  if (files && channel) files = files.filter((file) => localFileMatchesChannel(file, channel));
  if (!files && channel && !candidateLists.length) {
    const listed = [];
    for (const root of roots) await listMarkdownFiles(root, listed);
    files = listed.filter((file) => localFileMatchesChannel(file, channel));
  }
  return files;
}
async function localMarkdownSearch(built, startedAt, warning) {
  const fieldTermQuery = built.fieldOnlyQuery && !stripQuotes(built.effective.get('user') || '') && !normalizeChannel(built.effective.get('channel') || '') ? '' : (built.cleanQuery || built.effective.get('q') || '');
  const terms = plainTerms(fieldTermQuery);
  const roots = await existingLocalRoots(built.localRoots);
  const fieldCandidates = await localFieldCandidateFiles(roots, built.effective);
  const files = fieldCandidates || [];
  if (!fieldCandidates) {
    for (const root of roots) await listMarkdownFiles(root, files);
  }
  const results = [];
  for (const file of files) {
    let body = '';
    try { body = await readFile(file, 'utf8'); } catch { continue; }
    const haystack = `${path.relative(DATA_ROOT, file)}\n${body}`.toLowerCase();
    const hits = terms.length ? terms.reduce((sum, term) => sum + (haystack.includes(term) ? 1 : 0), 0) : 1;
    if (!hits && terms.length) continue;
    results.push({file: qmdUriForLocalMarkdown(file), title: titleFromMarkdown(file, body), score: terms.length ? hits / terms.length : 0.1, snippet: snippetForTerms(body, terms), backend: 'local-markdown-fallback'});
  }
  return finishSearch(results, built, startedAt, warning === null ? '' : (warning || 'QMD unavailable; searched local markdown files instead.'), 'local-markdown-fallback');
}
function resultFacets(results) {
  const channels = new Map();
  const users = new Map();
  for (const result of results) {
    const channel = normalizeChannel(result?.meta?.channel || '');
    const user = stripQuotes(result?.meta?.user || '');
    if (channel) channels.set(channel, (channels.get(channel) || 0) + 1);
    if (user) users.set(user, (users.get(user) || 0) + 1);
  }
  const sortFacet = (entries) => [...entries].map(([name, count]) => ({name, count})).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)).slice(0, 50);
  return {channels: sortFacet(channels.entries()), users: sortFacet(users.entries())};
}
async function finishSearch(results, built, startedAt, warning = '', backend = 'qmd') {
  const enriched = enrich(results);
  const filtered = applyFilters(enriched, built.effective);
  const userFilter = stripQuotes(built.effective.get('user') || '');
  const focused = userFilter ? filtered.map((result) => focusResultOnSlackSender(result, userFilter)) : filtered;
  const sorted = sortResults(focused, String(built.effective.get('sort') || 'relevance'));
  const limited = sorted.slice(0, built.limit);
  return {backend, warning, args: built.args, decorators: built.decorators, effectiveQuery: built.cleanQuery, relative: built.effective.get('relative') || '', effectiveDateFrom: built.effective.get('dateFrom') || '', effectiveDateTo: built.effective.get('dateTo') || '', resultFacets: resultFacets(limited), maxResults: MAX_RESULTS, fetched: results.length, matched: filtered.length, returned: limited.length, elapsedMs: Date.now() - startedAt, results: limited};
}
function argsForCollections(args, collections) {
  const next = [];
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === '-c' || args[index] === '--collection') { index += 1; continue; }
    next.push(args[index]);
  }
  for (const collection of collections) next.push('-c', collection);
  return next;
}
function mergeResultGroups(groups, limit) {
  const merged = [];
  const seen = new Set();
  let offset = 0;
  while (merged.length < limit) {
    let added = false;
    for (const group of groups) {
      const item = group.results[offset];
      if (!item) continue;
      const key = item.file || item.path || item.docid || `${item.title || ''}:${item.snippet || item.text || ''}`;
      if (!seen.has(key)) {
        seen.add(key);
        merged.push(item);
        added = true;
        if (merged.length >= limit) break;
      }
    }
    offset += 1;
    if (!added && groups.every((group) => offset >= group.results.length)) break;
  }
  return merged;
}
async function qmdSearchByCollectionOrFallback(built, startedAt) {
  const groups = [];
  const warnings = [];
  for (const collection of built.qmdCollections) {
    const args = argsForCollections(built.args, [collection]);
    const result = await runQmd(args, built.mode === 'hybrid' || built.mode === 'vec' ? 90000 : 60000);
    if (result.code !== 0) { warnings.push(`${collection} search failed (${result.code})`); continue; }
    try {
      const parsed = parseQmdJson(result.stdout);
      if (parsed.length) groups.push({collection, results: parsed});
      const semanticWarning = built.mode === 'lex' ? '' : semanticIndexWarning(result.stderr);
      if (semanticWarning) warnings.push(`${collection}: ${semanticWarning}`);
    } catch {
      warnings.push(`${collection} returned unusable output`);
    }
  }
  if (!groups.length) {
    if (built.mode !== 'lex') return qmdLexicalFallback(built, startedAt, warnings.join('; ') || `QMD ${built.mode} search failed`);
    return localMarkdownSearch(built, startedAt, warnings.length ? warnings.join('; ') : null);
  }
  const results = mergeResultGroups(groups, built.limit * Math.max(1, groups.length));
  const args = [...built.args, '--balanced-client-merge'];
  return finishSearch(results, {...built, args}, startedAt, warnings.join('; '), 'qmd');
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
  if (built.mode === 'lex' && built.fieldOnlyQuery && built.localRoots.length) return localMarkdownSearch(built, startedAt, null);
  if (built.qmdCollections.length > 1) return qmdSearchByCollectionOrFallback(built, startedAt);
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
  const conversations = await readJsonIfExists(path.join(DATA_ROOT, 'chunks/slack', SLACK_RUN_ID, 'conversations.json'), []);
  for (const conversation of Array.isArray(conversations) ? conversations : []) {
    const name = conversation.name || conversation.name_normalized || conversation.id;
    if (name) channels.set(name, {name, id: conversation.id || '', count: 0});
  }
  const profiles = await readJsonIfExists(path.join(DATA_ROOT, 'chunks/slack', SLACK_RUN_ID, 'user_profiles.json'), {});
  for (const [id, profile] of Object.entries(profiles || {})) {
    const name = profile.display_name || profile.real_name || profile.name || id;
    users.set(name, {name, id, realName: profile.real_name || '', slackName: profile.name || '', count: 0});
  }
  for (const file of await listMarkdownFiles(path.join(DATA_ROOT, 'raw/slack'))) {
    const rel = path.relative(path.join(DATA_ROOT, 'raw/slack'), file);
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
  for (const file of await listMarkdownFiles(path.join(DATA_ROOT, 'wiki/sources'))) {
    try {
      const body = await readFile(file, 'utf8');
      for (const match of body.matchAll(/\bchannel=([^\s`]+)/gi)) {
        const name = normalizeChannel(match[1]);
        if (!name) continue;
        const current = channels.get(name) || {name, id: '', count: 0};
        current.count += 1;
        channels.set(name, current);
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
    if (url.pathname === '/health') return json(res, 200, {ok: true, service: 'qmd-search', qmd: QMD, root: ROOT, dataRoot: DATA_ROOT, maxResults: MAX_RESULTS, defaultCollection: DEFAULT_COLLECTION, searchCacheMs: SEARCH_CACHE_MS, searchCacheSize: searchCache.size, hybridCandidateLimit: HYBRID_CANDIDATE_LIMIT});
    if (url.pathname === '/api/facets') return json(res, 200, await buildFacets());
    if (url.pathname === '/api/search') return handleSearch(req, res, url);
    if (url.pathname === '/api/get') return handleGet(req, res, url);
    return json(res, 404, {error: 'not found'});
  } catch (error) {
    return json(res, 500, {error: safeError(error)});
  }
});
server.listen(PORT, HOST, () => console.log(`QMD search listening on http://${HOST}:${PORT} maxResults=${MAX_RESULTS}`));
