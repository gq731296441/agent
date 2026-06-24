/** 前端消息渲染测试用例（Agent 测试控制台注入） */

import { handleAgentMessage } from './message-renderer.js';
import { state } from './config.js';

const DEMO_RUN_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
const DEMO_CONV_ID = '11111111-2222-3333-4444-555555555555';
const DEMO_SUBAGENT_ID = 'subagent-demo-0001-0002-0003-0004-000000000001';

function baseMeta(overrides = {}) {
  return {
    session_id: state.sessionId || 'demo-session-id',
    run_id: DEMO_RUN_ID,
    conversation_id: DEMO_CONV_ID,
    step_index: overrides.step_index ?? 1,
    sender: 'system',
    ...overrides,
  };
}

export const SUBAGENT_FIXTURES = {
  start: {
    type: 'subagent_start',
    content: {
      agent_mode: 'deep',
      run_id: DEMO_RUN_ID,
      conversation_id: DEMO_CONV_ID,
      tool_name: 'task',
      tool_call_id: 'call_demo_task_001',
      subagent_run_id: DEMO_SUBAGENT_ID,
      name: 'researcher',
      status: 'running',
      input_summary: '[researcher] 查找 MRM 待清理文档并汇总删除建议',
      args: {
        subagent_type: 'researcher',
        description: '查找 MRM 待清理文档并汇总删除建议',
      },
    },
  },
  end: {
    type: 'subagent_end',
    content: {
      agent_mode: 'deep',
      run_id: DEMO_RUN_ID,
      conversation_id: DEMO_CONV_ID,
      tool_name: 'task',
      tool_call_id: 'call_demo_task_001',
      subagent_run_id: DEMO_SUBAGENT_ID,
      name: 'researcher',
      status: 'completed',
      input_summary: '[researcher] 查找 MRM 待清理文档并汇总删除建议',
      final_report: '共发现 3 份待清理文档，建议按优先级分批删除。',
      truncated: false,
    },
  },
};

export function injectSubagentStart() {
  handleAgentMessage({ ...baseMeta({ step_index: 10 }), ...SUBAGENT_FIXTURES.start });
}

export function injectSubagentEnd() {
  handleAgentMessage({ ...baseMeta({ step_index: 11 }), ...SUBAGENT_FIXTURES.end });
}

export function injectSubagentFlow() {
  injectSubagentStart();
  setTimeout(() => injectSubagentEnd(), 600);
}

export function injectFixture(name) {
  if (name === 'subagent_start') return injectSubagentStart();
  if (name === 'subagent_end') return injectSubagentEnd();
  if (name === 'subagent_flow') return injectSubagentFlow();
}
