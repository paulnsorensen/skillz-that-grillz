# Hooks Catalog for Skill Controls

Rules in SKILL.md and CLAUDE.md guide the model. Hooks provide event-specific controls, but they do not guarantee 100% compliance.
Classify each hook as prevention, detection, or a reminder. Match the claim to the event's actual control point.

## The Skill + Hook + Command Trinity

A robust skill architecture can use all three:

- **Skill** — Progressive-disclosure knowledge that loads on demand
- **Hook** — Event-specific prevention, detection, or feedback
- **Command** — User-invoked workflow (slash command) for explicit activation

The snippets below are illustrative patterns, not drop-in hooks. Claude Code
passes hook input as a JSON payload on **stdin** (fields like `tool_name`,
`tool_input`, `prompt`), not env vars. Adapt before installing.

## Hook Categories

### 1. Skill Evaluation Reminder (Activation)

**Problem:** Skills frequently under-trigger — Claude skips skill evaluation for
tasks it thinks it can handle directly.

**Reminder:** A `UserPromptSubmit` hook can ask Claude to evaluate available skills.
It improves visibility but cannot prove that Claude evaluated or used a skill.
Community testing reports activation gains, but rates vary and are not an official benchmark.

```javascript
// .claude/hooks/force-skill-eval.js
const message = `MANDATORY: Before responding, evaluate whether any installed skill
is relevant to this request. Check all skill descriptions against the user's intent.
If a skill matches, read its SKILL.md BEFORE proceeding.`;
console.log(message);
```

**Tuned version** (keyword-filtered to avoid overhead on simple prompts):

```javascript
const fs = require('fs');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const prompt = payload.prompt === undefined ? '' : payload.prompt;
const skillKeywords = ['review', 'test', 'deploy', 'migrate', 'refactor', 'analyze'];
const shouldEval = skillKeywords.some(kw => prompt.toLowerCase().includes(kw));
if (shouldEval) {
  console.log('MANDATORY: Evaluate installed skills before proceeding.');
}
```

Reported cost (community, not benchmarked): ~$0.007/prompt, ~7s overhead.

### 2. Output Validation (Quality)

**Problem:** Skill says "always include tests" but Claude skips them.

**Detection:** A `PostToolUse` hook checks requirements after a write completes.
It cannot prevent that completed write. Return feedback on stderr with exit 2 so Claude can remediate it.

```javascript
// .claude/hooks/validate-output.js
const fs = require('fs');
const path = require('path');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const toolInput = payload.tool_input === undefined ? {} : payload.tool_input;
const filePath = toolInput.path === undefined
  ? (toolInput.file_path === undefined ? '' : toolInput.file_path)
  : toolInput.path;
if (!filePath) process.exit(0);

const ext = path.extname(filePath);
const dir = path.dirname(filePath);
const base = path.basename(filePath, ext);

// Rule: source files should have corresponding test files
const isSourceFile = ['.ts', '.tsx'].includes(ext)
  && !filePath.includes('.test.') && !filePath.includes('.spec.');

if (isSourceFile) {
  const testPatterns = [
    path.join(dir, `${base}.test${ext}`),
    path.join(dir, '__tests__', `${base}.test${ext}`),
  ];
  if (!testPatterns.some(p => fs.existsSync(p))) {
    console.error(`WARNING: ${filePath} was written without a test file.`);
    process.exit(2);
  }
}
```

### 3. Context Preprocessing (Efficiency)

**Problem:** Skill reads 10,000-line log, burning context on irrelevant lines.

**Prevention:** A `PreToolUse` hook can replace the pending read path before the tool runs.
Return `hookSpecificOutput.updatedInput`. Preserve every unchanged input field.

```javascript
// .claude/hooks/preprocess-context.js
const fs = require('fs');
const os = require('os');
const path = require('path');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const toolInput = payload.tool_input === undefined ? {} : payload.tool_input;
const pathKey = toolInput.path === undefined ? 'file_path' : 'path';
const filePath = toolInput[pathKey] === undefined ? '' : toolInput[pathKey];
if (!filePath) process.exit(0);

if (filePath.endsWith('.log')) {
  const content = fs.readFileSync(filePath, 'utf-8');
  const lines = content.split('\n');
  const filtered = lines.filter(l => /\b(ERROR|WARN|FATAL)\b/i.test(l));
  if (filtered.length < lines.length * 0.5) {
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'claude-preprocess-'));
    const preprocessed = path.join(tempDir, 'filtered.txt');
    fs.writeFileSync(preprocessed, `[${lines.length} lines → ${filtered.length}]\n\n${filtered.join('\n')}`);
    console.log(JSON.stringify({
      hookSpecificOutput: {
        hookEventName: 'PreToolUse',
        updatedInput: { ...toolInput, [pathKey]: preprocessed },
      },
    }));
  }
}
```

### 4. Banned Pattern Detection (Guardrails)

**Problem:** Skill says "never use console.log" but Claude does it anyway.

**Detection:** A `PostToolUse` hook scans a file after the write completes.
It reports violations for remediation; it does not undo or prevent the write.

```javascript
// .claude/hooks/banned-patterns.js
const fs = require('fs');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const toolInput = payload.tool_input === undefined ? {} : payload.tool_input;
const filePath = toolInput.path === undefined
  ? (toolInput.file_path === undefined ? '' : toolInput.file_path)
  : toolInput.path;
if (!filePath || !fs.existsSync(filePath)) process.exit(0);

const content = fs.readFileSync(filePath, 'utf-8');
const rules = [
  { pattern: /console\.log\(/g, message: 'Use project logger instead of console.log',
    exclude: ['.test.', '.spec.', 'scripts/'] },
  { pattern: /TODO|FIXME|HACK/g, message: 'Resolve TODO/FIXME before committing' },
];

const violations = [];
for (const rule of rules) {
  if (rule.exclude && rule.exclude.some(ex => filePath.includes(ex))) continue;
  const matches = content.match(rule.pattern);
  if (matches) violations.push(`  ${rule.message} (${matches.length}x)`);
}
if (violations.length > 0) {
  console.error(`Pattern violations in ${filePath}:\n${violations.join('\n')}`);
  process.exit(2);
}
```

### 5. Token Budget Warning (Cost Control)

**Problem:** Long sessions degrade silently. The interval between 50% and
auto-compaction is the "dumb zone."

**Fix:** `UserPromptSubmit` hook tracks prompt count and suggests compaction.

```javascript
// .claude/hooks/token-budget-check.js
const fs = require('fs');
const file = '/tmp/claude-prompt-counter.json';
let counter;
try {
  counter = JSON.parse(fs.readFileSync(file, 'utf-8'));
} catch (error) {
  if (error.code === 'ENOENT') {
    counter = { count: 0, lastCompact: Date.now() };
  } else {
    throw error;
  }
}
counter.count++;
fs.writeFileSync(file, JSON.stringify(counter));

if (counter.count % 20 === 0) {
  console.log(`Context check: ${counter.count} prompts. Consider /compact if responses feel imprecise.`);
}
```

## Hook Installation

Hooks use an event, a matcher group, a `hooks` array, and a command definition.
Match file-write checks to `Write|Edit`. Use `Read` for the preprocessing example.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          { "type": "command", "command": "node .claude/hooks/validate-output.js" }
        ]
      }
    ]
  }
}
```

Events used by this catalog are `UserPromptSubmit`, `PreToolUse`, and `PostToolUse`.
Claude Code also supports other events; check current documentation before selecting one.

Exit 0 accepts the hook result. Exit 2 sends stderr to Claude and blocks only when the event supports prevention.
For `PostToolUse`, the tool has already completed, so exit 2 supplies remediation feedback only.
Supported JSON output can make event-specific decisions or update a pending tool input.
On `UserPromptSubmit` and `SessionStart`, stdout is injected into Claude's context. Keep prompt-level hooks under five seconds.
