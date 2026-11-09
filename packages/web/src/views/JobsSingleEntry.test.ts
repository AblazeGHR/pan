import { expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
const root = path.resolve(import.meta.dirname, '..');
it('routes every Jobs alias directly to the designed Jobs page', () => {
  const source = fs.readFileSync(path.join(root, 'router.tsx'), 'utf8');
  expect(source).toMatch(/path: 'automation\/canonical', element: <Navigate replace to="\/jobs"/);
  expect(source).toMatch(/path: 'schedules',[\s\S]*?<Navigate replace to="\/jobs"/);
  expect(source).toMatch(/path: 'jobs',[\s\S]*?<JobsView/);
});
it('keeps all Action Group Jobs and Automation product literals English while leaving user data dynamic', () => {
  const targets = ['components/actions', 'components/jobs', 'components/automation'].flatMap(dir =>
    fs.readdirSync(path.join(root, dir)).filter(name => /\.tsx?$/.test(name) && !name.includes('.test.')).map(name => path.join(root, dir, name)));
  targets.push(...['ActionsView', 'UnifiedActionsView', 'JobsView', 'CanonicalJobsView', 'AutomationView'].map(name => path.join(root, 'views', name + '.tsx')));
  const failures: string[] = [];
  for (const file of targets) {
    const source = fs.readFileSync(file, 'utf8');
    expect(source).not.toContain('Canonical Jobs');
    const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    const visit = (node: ts.Node) => {
      if ((ts.isStringLiteralLike(node) || ts.isJsxText(node)) && /[\u3400-\u9fff]/.test(node.getText(tree))) failures.push(path.basename(file) + ':' + node.getText(tree));
      ts.forEachChild(node, visit);
    };
    visit(tree);
  }
  expect(failures).toEqual([]);
});
