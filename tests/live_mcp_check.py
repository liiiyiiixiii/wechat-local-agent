"""Real stdio protocol smoke test. Prints counts only, not private conversation contents.

Run with the service venv (it needs the `mcp` client package):
    private/agent-venv/bin/python tests/live_mcp_check.py
It exercises the exact compatibility entry that Codex is configured to launch.
"""
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from wxlocal.config import HOME, LEGACY, PYTHON, STATE


def require(condition, stage):
    if not condition:
        raise RuntimeError('acceptance_incomplete: '+stage)


async def main():
    args = ['-m','wxlocal']
    if not LEGACY:
        args.extend(['--home',str(HOME)])
    parameters = StdioServerParameters(command=str(PYTHON), args=args+['mcp'])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            discovered = await session.list_tools()
            expected = {'get_status', 'find_conversations', 'search_messages', 'get_context',
                        'read_attachment', 'get_changes', 'ack_changes'}
            require({t.name for t in discovered.tools} == expected, 'tool_discovery')

            async def call(name, args=None):
                out = await session.call_tool(name, args or {})
                require(not out.isError, 'tool_call')
                data = out.structuredContent
                if data is None:
                    data = json.loads(out.content[0].text)
                return data

            status = await call('get_status')
            report = {'stdio_initialized': True, 'discovered_tools': sorted(expected),
                      'initialized': status['initialized']}
            if not status['initialized'] or status['sync'].get('state') != 'ready':
                raise RuntimeError('acceptance_incomplete: index is not ready')
            conversations = await call('find_conversations', {'limit': 2})
            require(conversations['items'], 'no_conversations')
            found = await call('search_messages', {'message_type': 'file', 'limit': 200})
            candidate = next((m for m in found['items'] if any(
                a['extraction'] in ('extracted', 'ocr_extracted', 'plain_text') for a in m['attachments'])), None)
            require(candidate, 'no_readable_document_in_sample')
            asset = next(a for a in candidate['attachments']
                         if a['extraction'] in ('extracted', 'ocr_extracted', 'plain_text'))
            query = asset['name']
            searched = await call('search_messages',
                                  {'query': query, 'conversation_id': candidate['conversation_id'], 'limit': 50})
            require(any(x['id'] == candidate['id'] for x in searched['items']), 'filename_search')
            context = await call('get_context', {'message_id': candidate['id'], 'before': 2, 'after': 2})
            require(any(x['id'] == candidate['id'] for x in context['items']), 'message_context')
            document = await call('read_attachment', {'attachment_id': asset['id'], 'length': 1200})
            require(document['available'] and document['text'], 'attachment_text')
            consumer = 'protocol-smoke-' + str(int(time.time()))
            batch = await call('get_changes', {'consumer_id': consumer, 'replay_history': True, 'limit': 2})
            require(batch['items'], 'change_replay')
            confirmed = await call('ack_changes', {'consumer_id': consumer, 'cursor': batch['ack_cursor']})
            require(confirmed['confirmed_cursor'] == batch['ack_cursor'], 'acknowledgement')
            report.update(messages=status['messages'], history_search=True, context=True, document_read=True,
                          consumer_ack=True)
            STATE.mkdir(parents=True, exist_ok=True)
            (STATE / 'mcp-acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
