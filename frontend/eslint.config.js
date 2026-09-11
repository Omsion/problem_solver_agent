import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // 构建产物与测试覆盖率报告不参与检查
  globalIgnores(['dist', 'coverage']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // 本项目的数据加载/计时器模式会在 effect 内同步设置状态
      // （例如 setLoading(true)、setElapsed(0)）。这些状态是 effect 的
      // 必要组成部分，而不是从 props 派生的冗余状态，因此关闭该提示；
      // 其余 react-hooks 规则（依赖数组、refs 使用等）保持开启。
      'react-hooks/set-state-in-effect': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
    },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'src/test/**/*.{ts,tsx}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
  },
  {
    // UI 原语与工具模块会同时导出组件与钩子/常量（例如 confirm.tsx 里的
    // useConfirm、toast.tsx 里的 notify）。react-refresh 的"只导出组件"规则
    // 是开发期 HMR 优化，这类模块退化为整页刷新是可以接受的。
    files: [
      'src/components/ui/*.{ts,tsx}',
      'src/components/tasks/TaskCard.tsx',
      'src/components/output/lazy.tsx',
    ],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
])
