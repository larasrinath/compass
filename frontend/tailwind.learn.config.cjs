module.exports = {
  content: { relative: true, files: ['./src/learn/**/*.{ts,tsx}'] },
  important: '#compass-learn',
  corePlugins: { preflight: false },
  theme: { extend: {
    colors: {
      canvas: '#fbfaf6', surface: '#fff', subtle: '#f4f2ec', ink: '#26251f',
      body: '#4a473e', faint: '#8a8577', line: '#e7e4da',
      accent: { DEFAULT: '#33615a', strong: '#264b45', soft: '#e7efec' },
      sage: { DEFAULT: '#2f7a57', soft: '#e8f2eb' },
      amberdeep: '#8f6510', amber: { soft: '#f8efdb' },
      rust: { DEFAULT: '#a5482f', soft: '#f7e9e3' },
    },
    fontFamily: { sans: ['var(--font-body)'], display: ['var(--font-display)'] },
  } },
}
