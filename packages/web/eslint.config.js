import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import prettier from 'eslint-config-prettier';

export default tseslint.config(
  // Legacy Vanilla frontend is checked separately by the root TypeScript
  // compile hook; keep it out of the React lint/test surface.
  { ignores: ['dist/', 'static/', 'node_modules/', 'ts/'] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { window: 'readonly', document: 'readonly' },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_' },
      ],
    },
  },
  // eslint-config-prettier 9.x：默认导出即 flat config 对象（仅关掉与 Prettier
  // 冲突的规则）；旧 API `prettier.configs.disable` 已被移除，直接使用默认导出。
  prettier,
);
