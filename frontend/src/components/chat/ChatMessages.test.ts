import { describe, expect, it } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { normalizeConversationMath } from "./ChatMessages";

describe("normalizeConversationMath", () => {
  it("wraps bracket-wrapped display math in dollars", () => {
    const input = "[ \\min_{G_{sub}} \\left[ -I(G_{sub}, Y) + \\beta I(G_{sub}, G) \\right] ]";
    const out = normalizeConversationMath(input);
// 外层 `[ ... ]` 会变为行内数学；内层 \left[...\right] 保持合法。
    expect(out).toContain("$\\min_{G_{sub}} \\left[");
    expect(out).toContain("\\right]$");
  });

  it("wraps parenthesis-wrapped simple math in dollars", () => {
    expect(normalizeConversationMath("( \\beta X )")).toBe("$\\beta X$");
    expect(normalizeConversationMath("(G_{sub})")).toContain("$G_{sub}$");
  });

  it("turns bare subscripts into inline math", () => {
    expect(normalizeConversationMath("G_{sub} 是子图")).toContain("$G_{\\mathrm{sub}}$");
    expect(normalizeConversationMath("G_sub 简化形式")).toContain("$G_{\\mathrm{sub}}$");
  });

  it("leaves plain brackets and prose parentheses untouched", () => {
    expect(normalizeConversationMath("参考文献 [1] 和 (普通说明)")).toBe("参考文献 [1] 和 (普通说明)");
  });

  it("leaves valid $...$ math untouched", () => {
    const input = "目标函数 $\\min L$ 是最小值。";
    expect(normalizeConversationMath(input)).toBe(input);
  });

  it("converts LaTeX bracket delimiters used by the model", () => {
    const input = "设图 \\(G=(V,E)\\)，节点特征为：\n\n\\[\nX \\in \\mathbb{R}^{n \\times d}\n\\]";
    const out = normalizeConversationMath(input);

    expect(out).toContain("$G=(V,E)$");
    expect(out).toContain("$$\nX \\in \\mathbb{R}^{n \\times d}\n$$");
  });

  it("does not inject nested dollar delimiters into an existing math block", () => {
    const input = "\\[e_{vu} = \\text{LeakyReLU}\\left( a^T [h'_v \\parallel h'_u] \\right)\\]";
    const out = normalizeConversationMath(input);

    expect(out).toContain("$$\ne_{vu} = \\text{LeakyReLU}\\left( a^T [h'_v \\parallel h'_u] \\right)\n$$");
    expect(out).not.toContain("$h'_v");
  });

  it("normalizes the common bracket and nested-parenthesis formats", () => {
    const input = [
      "设图 (G=(V,E))，节点数为 $n$。",
      "[\nX \\in \\mathbb{R}^{n \\times d}\n]",
      "[ N(v)={u \\in V \\mid (u,v)\\in E} ]",
    ].join("\n\n");
    const out = normalizeConversationMath(input);

    expect(out).toContain("$G=(V,E)$");
    expect(out).toContain("$$\nX \\in \\mathbb{R}^{n \\times d}\n$$");
    expect(out).toContain("$N(v)={u \\in V \\mid (u,v)\\in E}$");
    expect(out).toContain("$n$");
    expect(normalizeConversationMath("节点数为 (n)，邻接矩阵为 (A)。")).toBe("节点数为 $n$，邻接矩阵为 $A$。");
  });

  it("does not rewrite formulas inside fenced code", () => {
    const input = "```text\n[G_{sub}]\n(G=(V,E))\n```";
    expect(normalizeConversationMath(input)).toBe(input);
  });

  it("produces KaTeX nodes for normalized chat content", () => {
    const input = "设图 (G=(V,E))。\n\n[\nX \\in \\mathbb{R}^{n \\times d}\n]";
    const html = renderToStaticMarkup(React.createElement(
      ReactMarkdown,
      { remarkPlugins: [remarkGfm, remarkMath], rehypePlugins: [rehypeKatex] },
      normalizeConversationMath(input),
    ));

    expect(html).toContain("katex");
    expect(html).toContain("katex-display");
  });
});
