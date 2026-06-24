/** 运营统计面板 */

import { statsApi } from './api.js';

function getDateRange() {
  const start = document.getElementById('stats-start')?.value || '';
  const end = document.getElementById('stats-end')?.value || '';
  return { start_date: start || undefined, end_date: end || undefined };
}

export async function loadStats() {
  const out = document.getElementById('stats-output');
  if (out) out.textContent = '加载中…';
  const range = getDateRange();

  try {
    const [overview, tasks, toolCalls] = await Promise.all([
      statsApi.overview(range.start_date, range.end_date),
      statsApi.tasks(range.start_date, range.end_date),
      statsApi.toolCalls(range.start_date, range.end_date),
    ]);
    const combined = {
      overview: overview.body,
      tasks: tasks.body,
      toolCalls: toolCalls.body,
    };
    if (out) out.textContent = JSON.stringify(combined, null, 2);
  } catch (e) {
    if (out) out.textContent = e.message;
  }
}

export async function loadAllStats() {
  const out = document.getElementById('stats-output');
  if (out) out.textContent = '加载全部统计…';
  const range = getDateRange();

  try {
    const results = await Promise.all([
      statsApi.overview(range.start_date, range.end_date),
      statsApi.users(range.start_date, range.end_date),
      statsApi.sessions(range.start_date, range.end_date),
      statsApi.messages(range.start_date, range.end_date),
      statsApi.toolCalls(range.start_date, range.end_date),
      statsApi.tasks(range.start_date, range.end_date),
    ]);
    const keys = ['overview', 'users', 'sessions', 'messages', 'toolCalls', 'tasks'];
    const combined = {};
    keys.forEach((k, i) => {
      combined[k] = results[i].body;
    });
    if (out) out.textContent = JSON.stringify(combined, null, 2);
  } catch (e) {
    if (out) out.textContent = e.message;
  }
}

export function initStatsPanel() {
  document.getElementById('btn-load-stats')?.addEventListener('click', loadStats);
  document.getElementById('btn-load-all-stats')?.addEventListener('click', loadAllStats);
}
