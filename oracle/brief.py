#!/usr/bin/env python3
"""brief.py — research brief 层（Alex 的 research step 的我们版）
对选中的某个 spike，产出一页 brief：别人说过啥 / 反直觉角度 / 开放问题 / 可挂的支柱叙事。
当前为文档化 stub —— brief 生成质量依赖 LLM + 检索，最佳实践是走 Claude 会话：

用法（推荐，在 Claude 会话里）：
    对选中的 spike，说「给这个 spike 出 research brief」，Claude 会：
    1. WebSearch / agent-reach 检索该话题近况（别人说过啥、原始出处）
    2. 找反直觉 / novel 角度（能挂上哪个支柱的 verification 叙事）
    3. 列开放问题（可进"难题池"）
    4. fact-check：每个数字/案例回一手源（RULES 硬线）

也可命令行传一个 spike 的 url/text 生成骨架 brief 模板：
    python3 brief.py --text "spike 文本" --url "..."
"""
import argparse

TEMPLATE = """# Research Brief — {title}

- **来源**：{url}
- **候选支柱**：P?（P1验证证明 / P2基准证据 / P3方法论 / P4圈内接话）

## 别人说过什么（回一手源）
- …

## 反直觉 / novel 角度（能挂哪条 verification 叙事）
- …

## 开放问题（可进难题池）
- …

## 我们的切口（一句可截图引用的断言）
- …

## 红线预检
- slogan verbatim / 产品用 service 名 / 无未确认 access·API·pricing / 数字回一手 / 无偷偷加承诺
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="")
    ap.add_argument("--url", default="")
    args = ap.parse_args()
    title = (args.text[:60] or "spike").strip()
    print(TEMPLATE.format(title=title, url=args.url or "…"))

if __name__ == "__main__":
    main()
