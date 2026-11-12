// @vitest-environment node
import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve, basename } from 'node:path';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';
import { SETTING_DESCRIPTIONS } from './settingsCatalog';

/** Follow the actual settings component imports, including a newly added panel. */
function settingSources(): string[] {
  const root = resolve(import.meta.dirname, 'AppSettingsModal.tsx');
  const pending = [root], visited = new Set<string>();
  while (pending.length) {
    const file = pending.pop()!;
    if (visited.has(file)) continue;
    visited.add(file);
    const source = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    for (const node of source.statements) {
      if (!ts.isImportDeclaration(node) || !ts.isStringLiteral(node.moduleSpecifier)) continue;
      const specifier = node.moduleSpecifier.text;
      const candidate = specifier.startsWith('.') ? resolve(dirname(file), specifier + '.tsx')
        : specifier.startsWith('@/') ? resolve(import.meta.dirname, '../..', specifier.slice(2) + '.tsx') : '';
      if (!candidate || !existsSync(candidate) || /SettingsSearchPanel/.test(candidate)) continue;
      if (candidate.includes('/layout/') || /[\\/]layout[\\/]/.test(candidate)
          || ['JobRetentionSettings.tsx', 'CodexNativePassthrough.tsx'].includes(basename(candidate))) pending.push(candidate);
    }
  }
  return [...visited];
}

function unregisteredDescriptions(text: string, file: string): string[] {
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const missing: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isJsxAttribute(node) && ['label', 'hint', 'description', 'title', 'placeholder', 'aria-label'].includes(node.name.getText(source))
        && node.initializer && ts.isStringLiteral(node.initializer)) {
      missing.push(`${basename(file)}: ${node.name.getText(source)} "${node.initializer.text}"`);
    }
    if (ts.isPropertyAssignment(node) && ['label', 'hint', 'description'].includes(node.name.getText(source))
        && ts.isStringLiteral(node.initializer)) missing.push(`${basename(file)}: ${node.name.getText(source)} "${node.initializer.text}"`);
    if (ts.isJsxText(node) && node.text.trim() && ts.isJsxElement(node.parent)
        && /^(label|legend|h[1-4])$/.test(node.parent.openingElement.tagName.getText(source))) {
      missing.push(`${basename(file)}: field caption "${node.text.trim()}"`);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return missing;
}

describe('actual settings UI registration', () => {
  it('reports a named error when a new actual control bypasses the shared description', () => {
    expect(unregisteredDescriptions('<SwitchRow label="New policy" hint="Preserve pending work" />', 'NewSettings.tsx'))
      .toEqual(['NewSettings.tsx: label "New policy"', 'NewSettings.tsx: hint "Preserve pending work"']);
  });

  it('requires every current panel field caption and hint to use the shared descriptions', () => {
    const missing = settingSources().flatMap((file) => unregisteredDescriptions(readFileSync(file, 'utf8'), file));
    expect(missing, `Unregistered setting UI descriptions:\n${missing.join('\n')}`).toEqual([]);
  });

  it('requires every literal description reference to have its original registered identity', () => {
    const ids = new Set(SETTING_DESCRIPTIONS.map((description) => description.id));
    const missing: string[] = [];
    for (const file of settingSources()) {
      const source = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const visit = (node: ts.Node) => {
        if (ts.isCallExpression(node) && node.expression.getText(source) === 'settingsText'
            && node.arguments[0] && ts.isStringLiteral(node.arguments[0]) && !ids.has(node.arguments[0].text)) {
          missing.push(`${basename(file)}: ${node.arguments[0].text}`);
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
    expect(missing).toEqual([]);
  });
});
