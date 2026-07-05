#!/usr/bin/env node
/**
 * Patch the live Telegram AI Assistant workflow to add:
 *   1. Tool — RAG Summaries   (search_project_summaries, calls qwen_rag_search_summaries)
 *   2. Tool — Project Structure (get_project_structure, reads /memory/project_structure.json)
 *
 * Run inside n8n container:
 *   node /tmp/patch-workflow-add-summary-tools.mjs
 */

import { execSync } from 'child_process';
import { readFileSync, writeFileSync } from 'fs';

const AGENT_WF_ID = '13329864-5514-4f83-acf9-a4610cd1e903';
const SUMMARIES_WF_ID = 'rags4444-0000-4000-0000-000000000001';
const TOOL_SUMMARIES_ID = 'tool0001-0012-4000-0000-000000000012';
const TOOL_STRUCTURE_ID = 'tool0001-0013-4000-0000-000000000013';

function run(cmd) {
  try {
    return execSync(cmd, { encoding: 'utf8' });
  } catch (e) {
    console.error('Command failed:', cmd);
    console.error(e.stderr || e.message);
    process.exit(1);
  }
}

console.log('Exporting live workflow...');
run(`n8n export:workflow --id=${AGENT_WF_ID} --output=/tmp/main-agent-live.json`);

const raw = readFileSync('/tmp/main-agent-live.json', 'utf8');
const data = JSON.parse(raw);
const wf = Array.isArray(data) ? data[0] : data;
const nodes = wf.nodes;
const connections = wf.connections;

// Check if already patched
if (nodes.some(n => n.id === TOOL_SUMMARIES_ID)) {
  console.log('Already patched — nothing to do.');
  process.exit(0);
}

const toolSummaries = {
  parameters: {
    name: 'search_project_summaries',
    description: "Search synthesized high-level summaries of project files, directories, and whole projects. Use for overview questions: 'What does Learngentic do?', 'What is the purpose of X?', 'What are the major subsystems of Y?'. Pass level='project' for whole-project overviews, level='directory' for component/subsystem answers, level='file' for file-level purpose. Leave level empty to search all summary levels.",
    workflowId: { __rl: true, mode: 'id', value: SUMMARIES_WF_ID },
    fields: {
      values: [
        { name: 'query', stringValue: "={{ $fromAI('query', 'Natural language question about a project, component, or file purpose') }}" },
        { name: 'level', stringValue: "={{ $fromAI('level', 'Optional: project | directory | file. Leave empty to search all summary levels.') }}" },
        { name: 'project', stringValue: "={{ $fromAI('project', 'Optional: project name to restrict results, e.g. Learngentic') }}" }
      ]
    }
  },
  id: TOOL_SUMMARIES_ID,
  name: 'Tool — RAG Summaries',
  type: '@n8n/n8n-nodes-langchain.toolWorkflow',
  typeVersion: 1.2,
  position: [4400, 1264]
};

const structureCode = `const fs = require('fs');
const STRUCTURE_FILE = '/memory/project_structure.json';
const projectFilter = ($input.first && $input.first().json && $input.first().json.project) || '';

let structure;
try {
  structure = JSON.parse(fs.readFileSync(STRUCTURE_FILE, 'utf8'));
} catch (e) {
  return [{ json: { output: 'Project structure manifest not yet built. Run the rag-build-structure webhook to generate it.' } }];
}

const allProjects = structure.projects || {};
const names = Object.keys(allProjects);

if (projectFilter && allProjects[projectFilter]) {
  const p = allProjects[projectFilter];
  const lines = [
    'Project: ' + projectFilter,
    'Path: ' + p.host_path,
    'Files: ' + p.file_count,
    'Languages: ' + Object.entries(p.languages || {}).map(e => e[0] + '(' + e[1] + ')').join(', '),
    'Top-level directories: ' + (p.top_level_dirs || []).join(', '),
    'Top-level files: ' + (p.top_level_files || []).join(', '),
  ].join('\\n');
  return [{ json: { output: lines } }];
}

const lines = ['Available projects (pass project name for details):'];
for (const name of names) {
  const p = allProjects[name];
  const langs = Object.keys(p.languages || {}).slice(0, 4).join(', ');
  lines.push('  ' + name + ' — ' + p.file_count + ' files | languages: ' + langs + ' | dirs: ' + (p.top_level_dirs || []).join(', '));
}
return [{ json: { output: lines.join('\\n') } }];`;

const toolStructure = {
  parameters: {
    jsCode: structureCode,
    name: 'get_project_structure',
    description: "Returns the file/directory structure of projects indexed in the project RAG. Use for navigation questions: 'Where is the backend?', 'What directories does Learngentic have?', 'What top-level files does X have?'. Pass project name for details on a specific project, or leave empty for an overview of all available projects."
  },
  id: TOOL_STRUCTURE_ID,
  name: 'Tool — Project Structure',
  type: '@n8n/n8n-nodes-langchain.toolCode',
  typeVersion: 1.1,
  position: [4560, 1264]
};

nodes.push(toolSummaries);
nodes.push(toolStructure);

function addToolConn(src, target) {
  if (!connections[src]) connections[src] = {};
  if (!connections[src].ai_tool) connections[src].ai_tool = [[]];
  connections[src].ai_tool[0].push({ node: target, type: 'ai_tool', index: 0 });
}

addToolConn('Tool — RAG Summaries', 'Knowledge Agent');
addToolConn('Tool — RAG Summaries', 'General Agent');
addToolConn('Tool — Project Structure', 'Knowledge Agent');

// Update search_project_rag description
for (const node of nodes) {
  if (node.id === 'rag00016-0000-4000-0000-000000000016') {
    node.parameters.toolDescription =
      'Search raw project file chunks (code, docs, configs) by semantic similarity. ' +
      "Best for implementation questions: 'How does X work?', 'Where is Y implemented?', 'What does Z() do?'. " +
      "For overview questions ('What does this project do?', 'What are the subsystems?') use search_project_summaries instead.";
    break;
  }
}

const out = JSON.stringify(Array.isArray(data) ? [wf] : wf, null, 2);
writeFileSync('/tmp/main-agent-patched.json', out);

console.log('Importing patched workflow...');
run(`n8n import:workflow --input=/tmp/main-agent-patched.json`);
console.log('Publishing...');
run(`n8n publish:workflow --id=${AGENT_WF_ID}`);
console.log('Done.');
