import js from '@eslint/js'
import globals from 'globals'

// §3.1 / P6 gate: replies are rendered by lib/markdown.jsx as React elements. Raw HTML injection
// is banned outright, so unsanitized text can never reach the DOM as markup.
const NO_RAW_HTML = [
  'error',
  { selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']", message: 'Render text through lib/markdown.jsx instead.' },
  { selector: "MemberExpression[property.name=/^(innerHTML|outerHTML)$/]", message: 'No raw HTML: build elements instead.' },
  { selector: "CallExpression[callee.property.name='insertAdjacentHTML']", message: 'No raw HTML: build elements instead.' },
]

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      ...js.configs.recommended.rules,
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]', args: 'none' }],
      'no-restricted-syntax': NO_RAW_HTML,
    },
  },
]
