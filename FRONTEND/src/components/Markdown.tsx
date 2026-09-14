/**
 * Markdown 渲染组件。
 *
 * 后端报告由大模型生成 Markdown（见 `core/prompts.py` 的 REPORT_SYSTEM_PROMPT），
 * 这里负责渲染并套用 Cohere 排版。表格支持来自 remark-gfm——
 * Agent 的报告大量使用 Markdown 表格输出指标排名。
 */

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface MarkdownProps {
  content: string
  className?: string
}

export function Markdown({ content, className }: MarkdownProps) {
  if (!content?.trim()) {
    return <div className="empty">暂无内容</div>
  }

  return (
    <div className={`markdown ${className ?? ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // 外链统一新窗口打开并加 rel，避免反向导航与安全问题
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer noopener" />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
