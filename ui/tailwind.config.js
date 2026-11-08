/** Tailwind 只负责布局与间距，组件交给 Naive UI。
 *  preflight 关掉：它会把 button、input 的基础样式清空，与 Naive UI 打架，
 *  表现是按钮变透明、输入框失去边框 —— 这类问题排查起来比省下的那点 CSS 贵得多。
 */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#0a0c10',
          900: '#0e1116',
          850: '#12161c',
          800: '#161b22',
          700: '#1e242d',
          600: '#2a323d',
          400: '#5c6878',
          300: '#8a94a6',
          200: '#b6bfcc',
          100: '#e3e8ef',
        },
        bili: {
          500: '#fb7299',
          400: '#ff8fae',
          600: '#e5557f',
        },
        ok: '#3dd68c',
        warn: '#f5a524',
        danger: '#f4456b',
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Microsoft YaHei', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
