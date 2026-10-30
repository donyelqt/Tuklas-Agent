import fs from 'fs';

function dropLine(path: string, commentLineWithIndent: string): number {
  let src = fs.readFileSync(path, 'utf8');
  const needle = commentLineWithIndent + '\r\n';
  if (!src.includes(needle)) {
    console.error('NOT FOUND:', JSON.stringify(commentLineWithIndent.slice(0, 50)));
    return 0;
  }
  src = src.replace(needle, '');
  fs.writeFileSync(path, src, 'utf8');
  return 1;
}

let n = 0;
// turn.ts
n += dropLine('apps/web/src/components/explorer/turn.ts', '  /** The document the question was asked against. */');
n += dropLine('apps/web/src/components/explorer/turn.ts', '  /** The question as it was asked, trimmed. */');
n += dropLine('apps/web/src/components/explorer/turn.ts', "  /** The terminal `done` event's AskResponse, or null if none arrived. */");
// types.ts
n += dropLine('apps/web/src/components/explorer/types.ts', '/** The tree response: a root plus the root\'s children. */');
console.log(n, 'edits applied');
