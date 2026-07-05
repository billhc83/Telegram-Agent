#!/usr/bin/env python3
"""
Patch the live Telegram AI Assistant workflow to add:
  1. Tool — RAG Summaries   (search_project_summaries, calls qwen_rag_search_summaries)
  2. Tool — Project Structure (get_project_structure, reads /memory/project_structure.json)

Both tools connect to the Knowledge Agent (f2a3b4c5-...) via ai_tool.
Tool — RAG Summaries also connects to the General Agent (b0c1d2e3-...) since
Tool — RAG Search already does.

Run:
  docker cp scripts/patch-workflow-add-summary-tools.py telegram-agent-n8n-1:/tmp/
  docker exec telegram-agent-n8n-1 python3 /tmp/patch-workflow-add-summary-tools.py
"""

import json, subprocess, sys, os

AGENT_WF_ID = '13329864-5514-4f83-acf9-a4610cd1e903'
SUMMARIES_WF_ID = 'rags4444-0000-4000-0000-000000000001'
KNOWLEDGE_AGENT_ID = 'f2a3b4c5-d6e7-89ab-f012-012345678901'
GENERAL_AGENT_ID = 'b0c1d2e3-f4a5-0123-789a-890123456789'

TOOL_SUMMARIES_ID = 'tool0001-0012-4000-0000-000000000012'
TOOL_STRUCTURE_ID = 'tool0001-0013-4000-0000-000000000013'

def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print('STDERR:', result.stderr[:500])
        sys.exit(1)
    return result.stdout

# Export live workflow
print('Exporting live workflow...')
run(['n8n', 'export:workflow', f'--id={AGENT_WF_ID}', '--output=/tmp/main-agent-live.json'])

with open('/tmp/main-agent-live.json') as f:
    data = json.load(f)

# n8n export wraps in a list
if isinstance(data, list):
    wf = data[0]
else:
    wf = data

nodes = wf['nodes']
connections = wf['connections']

# Check if already patched
existing_ids = {n['id'] for n in nodes}
if TOOL_SUMMARIES_ID in existing_ids:
    print('Already patched — nothing to do.')
    sys.exit(0)

# --- New nodes ---

tool_summaries = {
    "parameters": {
        "name": "search_project_summaries",
        "description": "Search synthesized high-level summaries of project files, directories, and whole projects. Use for overview questions: 'What does Learngentic do?', 'What is the purpose of X?', 'What are the major subsystems of Y?'. Pass level='project' for whole-project overviews, level='directory' for component/subsystem answers, level='file' for file-level purpose. Leave level empty to search all summary levels.",
        "workflowId": {"__rl": True, "mode": "id", "value": SUMMARIES_WF_ID},
        "fields": {
            "values": [
                {
                    "name": "query",
                    "stringValue": "={{ $fromAI('query', 'Natural language question about a project, component, or file purpose') }}"
                },
                {
                    "name": "level",
                    "stringValue": "={{ $fromAI('level', 'Optional: project | directory | file. Leave empty to search all summary levels.') }}"
                },
                {
                    "name": "project",
                    "stringValue": "={{ $fromAI('project', 'Optional: project name to restrict results, e.g. Learngentic') }}"
                }
            ]
        }
    },
    "id": TOOL_SUMMARIES_ID,
    "name": "Tool — RAG Summaries",
    "type": "@n8n/n8n-nodes-langchain.toolWorkflow",
    "typeVersion": 1.2,
    "position": [4400, 1264]
}

tool_structure = {
    "parameters": {
        "jsCode": (
            "const fs = require('fs');\n"
            "const STRUCTURE_FILE = '/memory/project_structure.json';\n"
            "\n"
            "// project is optional — from $fromAI or empty string\n"
            "const projectFilter = ($input.first && $input.first().json && $input.first().json.project) || '';\n"
            "\n"
            "let structure;\n"
            "try {\n"
            "  structure = JSON.parse(fs.readFileSync(STRUCTURE_FILE, 'utf8'));\n"
            "} catch (e) {\n"
            "  return [{ json: { output: 'Project structure manifest not yet built. Try asking to build it first.' } }];\n"
            "}\n"
            "\n"
            "const allProjects = structure.projects || {};\n"
            "const names = Object.keys(allProjects);\n"
            "\n"
            "if (projectFilter && allProjects[projectFilter]) {\n"
            "  const p = allProjects[projectFilter];\n"
            "  const lines = [\n"
            "    'Project: ' + projectFilter,\n"
            "    'Path: ' + p.host_path,\n"
            "    'Files: ' + p.file_count,\n"
            "    'Languages: ' + Object.entries(p.languages || {}).map(function(e) { return e[0] + '(' + e[1] + ')'; }).join(', '),\n"
            "    'Top-level directories: ' + (p.top_level_dirs || []).join(', '),\n"
            "    'Top-level files: ' + (p.top_level_files || []).join(', '),\n"
            "  ].join('\\n');\n"
            "  return [{ json: { output: lines } }];\n"
            "}\n"
            "\n"
            "// Return overview of all projects\n"
            "const lines = ['Available projects:'];\n"
            "for (const name of names) {\n"
            "  const p = allProjects[name];\n"
            "  const langs = Object.keys(p.languages || {}).slice(0, 3).join(', ');\n"
            "  lines.push('  ' + name + ' — ' + p.file_count + ' files, ' + langs + ' — dirs: ' + (p.top_level_dirs || []).join(', '));\n"
            "}\n"
            "return [{ json: { output: lines.join('\\n') } }];"
        ),
        "name": "get_project_structure",
        "description": "Returns the file/directory structure of projects indexed in the project RAG. Use for navigation questions: 'Where is the backend?', 'What directories does Learngentic have?', 'What are the top-level files in X?'. Pass project name to get details for a specific project, or leave empty for an overview of all projects."
    },
    "id": TOOL_STRUCTURE_ID,
    "name": "Tool — Project Structure",
    "type": "@n8n/n8n-nodes-langchain.toolCode",
    "typeVersion": 1.1,
    "position": [4560, 1264]
}

nodes.append(tool_summaries)
nodes.append(tool_structure)

# --- New connections ---
# Both tools → Knowledge Agent via ai_tool
# Tool Summaries also → General Agent

def add_tool_connection(src_name, agent_name):
    if src_name not in connections:
        connections[src_name] = {}
    if 'ai_tool' not in connections[src_name]:
        connections[src_name]['ai_tool'] = [[]]
    connections[src_name]['ai_tool'][0].append({
        'node': agent_name,
        'type': 'ai_tool',
        'index': 0
    })

add_tool_connection('Tool — RAG Summaries', 'Knowledge Agent')
add_tool_connection('Tool — RAG Summaries', 'General Agent')
add_tool_connection('Tool — Project Structure', 'Knowledge Agent')

# Update search_project_rag description to mention it searches chunks
for node in nodes:
    if node.get('id') == 'rag00016-0000-4000-0000-000000000016':
        node['parameters']['toolDescription'] = (
            "Search raw project file chunks (code, docs, configs) by semantic similarity. "
            "Best for implementation questions: 'How does X work?', 'Where is Y implemented?', 'What does Z() do?'. "
            "For high-level questions ('What does this project do?', 'What are the subsystems?') use search_project_summaries instead."
        )
        break

wf['nodes'] = nodes
wf['connections'] = connections

out = json.dumps(wf if not isinstance(data, list) else [wf], indent=2)
with open('/tmp/main-agent-patched.json', 'w') as f:
    f.write(out)

print('Importing patched workflow...')
run(['n8n', 'import:workflow', '--input=/tmp/main-agent-patched.json'])
print('Publishing...')
run(['n8n', 'publish:workflow', f'--id={AGENT_WF_ID}'])
print('Done.')
