import { Children, cloneElement, isValidElement, type CSSProperties, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Element, Root } from "hast";
import styles from "./conversation-workspace.module.css";

type TableElementProps = { children?: ReactNode; style?: CSSProperties };

// Each answer lives beneath the page heading. Keep its hierarchy, without skipped heading levels.
function readableHeadings() {
  return (tree: Root) => {
    const headings: Element[] = [];
    const collect = (node: Root | Element) => {
      for (const child of node.children) {
        if (child.type !== "element") continue;
        if (/^h[1-6]$/.test(child.tagName)) headings.push(child);
        collect(child);
      }
    };
    collect(tree);
    if (!headings.length) return;
    const firstLevel = Math.min(...headings.map((heading) => Number(heading.tagName.slice(1))));
    let previous = 1;
    for (const heading of headings) {
      const level = Math.min(6, previous + 1, Number(heading.tagName.slice(1)) - firstLevel + 2);
      heading.tagName = `h${level}`;
      previous = level;
    }
  };
}

function textContent(value: ReactNode): string {
  return Children.toArray(value).map((child) => {
    if (typeof child === "string" || typeof child === "number") return String(child);
    return isValidElement<TableElementProps>(child) ? textContent(child.props.children) : "";
  }).join("");
}

// Presentation only: a complete scalar column can align numerically; explicit Markdown alignment wins.
function alignScalarColumns(children: ReactNode): ReactNode {
  const sections = Children.toArray(children);
  const body = sections.find((section) => isValidElement(section) && section.type === "tbody");
  if (!isValidElement<TableElementProps>(body)) return children;
  const rows = Children.toArray(body.props.children).filter(isValidElement<TableElementProps>);
  const cells = rows.map((row) => Children.toArray(row.props.children).filter(isValidElement<TableElementProps>));
  if (!cells.length) return children;
  const scalar = /^(?:[$€£¥]\s*)?[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*(?:%|美元|人民币|元|USD|CNY|EUR|GBP))?$/i;
  const numericColumns = new Set(cells[0].map((_, index) => index).filter((index) =>
    cells.every((row) => row[index] && scalar.test(textContent(row[index].props.children).trim()))));
  if (!numericColumns.size) return children;
  return sections.map((section) => {
    if (!isValidElement<TableElementProps>(section) || !["thead", "tbody"].includes(String(section.type))) return section;
    return cloneElement(section, {}, Children.map(section.props.children, (row) => {
      if (!isValidElement<TableElementProps>(row)) return row;
      return cloneElement(row, {}, Children.map(row.props.children, (cell, index) => {
        if (!isValidElement<TableElementProps>(cell) || !numericColumns.has(index) || cell.props.style?.textAlign) return cell;
        return cloneElement(cell, { style: { ...cell.props.style, textAlign: "right" } });
      }));
    }));
  });
}

const components: Components = {
  ol: ({ node: _node, children, start, style, ...props }) => {
    const itemCount = Children.toArray(children).filter(isValidElement).length;
    const digits = String(Math.max(start ?? 1, (start ?? 1) + itemCount - 1)).length;
    return <ol {...props} start={start} style={{ ...style, paddingInlineStart: `calc(${digits}ch + 0.6em)` }}>{children}</ol>;
  },
  table: ({ children }) => <div className={styles.tableScroll} role="group" tabIndex={0} aria-label="回答中的表格">
    <table>{alignScalarColumns(children)}</table>
  </div>,
  code: ({ children, className, ...props }) => {
    const text = textContent(children);
    const reference = !className && !/[\r\n]/.test(text) && (
      /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(text)
      || /^[^\r\n<>]+\.(?:md|txt|pdf|docx?|xlsx?|csv|eml|png|jpe?g|webp)$/i.test(text)
    );
    // Keep code semantics and text intact; file names and email addresses need no code-chip decoration.
    const { node: _node, ...elementProps } = props;
    return <code {...elementProps} className={reference ? styles.referenceText : className}>{children}</code>;
  },
  input: ({ node: _node, ...props }) => <input {...props} aria-label={props.checked ? "已完成事项" : "未完成事项"} />,
};

export function MessageMarkdown({ content }: { content: string }) {
  return <ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} rehypePlugins={[readableHeadings]} components={components}>{content}</ReactMarkdown>;
}
