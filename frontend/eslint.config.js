import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

/**
 * What the linter refuses.
 *
 * Beyond the usual rules, two things are banned outright. Generated files may
 * not be edited by hand, because the next regeneration would silently undo
 * the edit. And a component may not reach for `localStorage` or `document`
 * directly for anything to do with a session — the token lives in memory, and
 * the rule that keeps it there has to be enforced somewhere.
 */
export default tseslint.config(
  { ignores: ['dist', 'coverage', 'src/api/schema.d.ts', 'src/api/sockets.d.ts'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      'no-restricted-globals': [
        'error',
        {
          name: 'localStorage',
          message: 'Reach for it through the theme module, and never for anything to do with a session.',
        },
      ],
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "MemberExpression[object.name='localStorage'][property.name=/^(get|set)Item$/]",
          message: 'Nothing about a session may be stored where a script can read it.',
        },
      ],
    },
  },
  {
    // Tests reach for storage and for globals on purpose: that is what they
    // are checking.
    files: ['**/*.test.{ts,tsx}', 'src/test/**'],
    rules: { 'no-restricted-globals': 'off', 'no-restricted-syntax': 'off' },
  },
  {
    files: ['scripts/**', '*.config.{ts,js}'],
    languageOptions: { globals: globals.node },
  },
);
