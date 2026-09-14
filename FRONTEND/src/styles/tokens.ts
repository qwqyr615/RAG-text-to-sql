/**
 * Cohere 设计令牌 —— 严格按 `FRONTEND/DESIGN.md` 落地。
 *
 * 说明：CohereText / Unica77 Cohere Web 是 Cohere 的私有字体，无法直接引用，
 * 因此按 DESIGN.md 给出的 fallback 链降级为开源等价字体：
 *   - Display  : CohereText   → Space Grotesk → Inter → system-ui
 *   - Body     : Unica77      → Inter → Arial → system-ui
 *   - Mono     : CohereMono   → JetBrains Mono → Arial → system-ui
 * 视觉性格（衬线标题 + 无衬线正文）得以保留。
 *
 * 设计原则（来自 DESIGN.md）：
 *   - 22px 是签名圆角，主卡片/主容器必须用它
 *   - 近乎无阴影：层次靠背景色对比与描边，不靠 box-shadow
 *   - 色彩极度克制：黑白 + 冷灰，紫色只用于整幅区块（绝不用于卡片背景）
 *   - 交互蓝 #1863dc 只用于 hover / focus 状态
 *   - 正文不用 700+ 字重，只用 400–500
 */

export const colors = {
  /** Cohere Black —— 主标题与最高强调 */
  black: '#000000',
  /** Near Black —— 标准正文/链接色，比纯黑柔和 */
  nearBlack: '#212121',
  /** Deep Dark —— 带蓝调的近黑，用于导航与深色区文字 */
  deepDark: '#17171c',

  /** Interaction Blue —— 唯一彩色动作色，仅 hover/focus */
  interactionBlue: '#1863dc',
  /** Focus Purple —— 输入框聚焦描边 */
  focusPurple: '#9b60aa',
  /** Ring Blue 50% —— 键盘聚焦外环 */
  ringBlue: 'rgba(76, 110, 230, 0.5)',

  /** Pure White —— 页面背景与卡片surface */
  white: '#ffffff',
  /** Snow —— 轻微抬升的 surface 与浅色区块 */
  snow: '#fafafa',
  /** Lightest Gray —— 卡片描边与最轻的分隔线 */
  lightestGray: '#f2f2f2',

  /** Muted Slate —— 弱化文字（带蓝紫调的冷灰） */
  mutedSlate: '#93939f',
  /** Border Cool —— 标准区块与列表项描边 */
  borderCool: '#d9d9dd',
  /** Border Light —— 更浅的描边variant */
  borderLight: '#e5e7eb',

  /** 深紫渐变区块（整幅） */
  purpleGradient: 'linear-gradient(160deg, #2b1a4a 0%, #1a1030 55%, #0d0a18 100%)',
  /** 深色页脚渐变 */
  footerGradient: 'linear-gradient(180deg, #1a1030 0%, #0a0810 100%)',

  /** 语义色（克制使用，仅用于状态提示） */
  danger: '#b30000',
  success: '#1a7f4b',
} as const

export const radii = {
  /** 锐利：导航元素、小标签、分页 */
  sharp: '4px',
  /** 舒适：对话框、次级容器、小卡片 */
  comfortable: '8px',
  /** 宽松：特色容器、中等卡片 */
  generous: '16px',
  /** 大：大型特性卡片 */
  large: '20px',
  /** 签名圆角：主卡片、主图、主容器 —— THE Cohere radius */
  signature: '22px',
  /** 药丸：按钮、标签、状态指示 */
  pill: '9999px',
} as const

export const fonts = {
  display: "'Space Grotesk', 'Inter', ui-sans-serif, system-ui, serif",
  body: "'Inter', Arial, ui-sans-serif, system-ui, sans-serif",
  mono: "'JetBrains Mono', 'CohereMono', Arial, ui-sans-serif, system-ui, monospace",
} as const

/**
 * 字阶（来自 DESIGN.md 的 Typography Hierarchy 表）。
 * 负字距只用于 display 级别（60–72px）。
 */
export const typography = {
  displayHero: { size: '72px', weight: 400, lineHeight: 1.0, letterSpacing: '-1.44px' },
  displaySecondary: { size: '60px', weight: 400, lineHeight: 1.0, letterSpacing: '-1.2px' },
  sectionHeading: { size: '48px', weight: 400, lineHeight: 1.2, letterSpacing: '-0.48px' },
  subHeading: { size: '32px', weight: 400, lineHeight: 1.2, letterSpacing: '-0.32px' },
  featureTitle: { size: '24px', weight: 400, lineHeight: 1.3, letterSpacing: 'normal' },
  bodyLarge: { size: '18px', weight: 400, lineHeight: 1.4, letterSpacing: 'normal' },
  body: { size: '16px', weight: 400, lineHeight: 1.5, letterSpacing: 'normal' },
  buttonMedium: { size: '14px', weight: 500, lineHeight: 1.71, letterSpacing: 'normal' },
  caption: { size: '14px', weight: 400, lineHeight: 1.4, letterSpacing: 'normal' },
  uppercaseLabel: { size: '14px', weight: 400, lineHeight: 1.4, letterSpacing: '0.28px' },
  small: { size: '12px', weight: 400, lineHeight: 1.4, letterSpacing: 'normal' },
} as const

/** 间距系统：基准 8px */
export const spacing = {
  xxs: '2px',
  xs: '6px',
  sm: '8px',
  md: '12px',
  lg: '16px',
  xl: '20px',
  xxl: '24px',
  xxxl: '32px',
  section: '56px',
  sectionLarge: '60px',
} as const

/** 响应式断点：DESIGN.md 声明原站有 26 个断点，这里保留 6 个主档位 */
export const breakpoints = {
  smallMobile: '425px',
  mobile: '640px',
  largeMobile: '768px',
  tablet: '1024px',
  desktop: '1440px',
  largeDesktop: '2560px',
} as const

/**
 * ECharts 统一主题。
 * 图表配色必须落在设计系统内：主色交互蓝，辅助冷灰，
 * 绝不引入暖色（DESIGN.md 明确要求调色板严格冷色）。
 */
export const chartTheme = {
  color: [
    '#1863dc',
    '#4c6ee6',
    '#9b60aa',
    '#17171c',
    '#93939f',
    '#d9d9dd',
    '#6b7fbf',
    '#5a4a7a',
  ],
  backgroundColor: 'transparent',
  textStyle: { fontFamily: fonts.body, color: colors.nearBlack },
  title: { textStyle: { color: colors.black, fontWeight: 400 } },
  grid: { left: 56, right: 32, top: 56, bottom: 48, containLabel: true },
  axisLine: { lineStyle: { color: colors.borderCool } },
  axisLabel: { color: colors.mutedSlate },
  splitLine: { lineStyle: { color: colors.lightestGray } },
  tooltip: {
    backgroundColor: colors.white,
    borderColor: colors.borderCool,
    borderWidth: 1,
    textStyle: { color: colors.nearBlack, fontSize: 13 },
  },
  legend: { textStyle: { color: colors.mutedSlate } },
} as const
