"""模型输出的结构约束。

一个刻意的偏离：**不让模型生成字符偏移量**，只让它给原文片段（quote），
偏移由程序 `text.find()` 算出来。

原因很实际——让模型数第几个字符，它会一本正经地给错，而且错得很像对的。
我们真正需要的约束是"这段文字必须真的出现在原文里"，这件事程序能百分之百验证；
让模型顺便给出偏移量，只是给校验增加噪声。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ItemKindLiteral = Literal["note", "idea", "link", "file", "todo"]


class QuoteSpan(BaseModel):
    """一段从原文摘出来的文字。"""

    quote: str = Field(description="原文中逐字连续的一段话，不得改写、不得省略、不得拼接")


class ItemAnalysis(BaseModel):
    """单条记录的分析结果。字段少而硬，便于校验。"""

    kind: ItemKindLiteral = Field(description="记录类型：note 笔记 / idea 想法 / link 链接 / file 文件 / todo 待办")
    title: str = Field(description="不超过 20 字的标题，概括这条记录在说什么")
    summary: str = Field(description="不超过 120 字的一句话摘要，只描述内容，不要写任何数量词")
    tags: list[str] = Field(description="3 到 6 个主题标签，每个 2 到 6 字，用于归类与检索")
    key_points: list[QuoteSpan] = Field(
        description="最多 5 条重点，每条必须是原文中的连续片段"
    )
    todos: list[QuoteSpan] = Field(
        description="最多 5 条待办，每条必须是原文中表达了'要做某事'的连续片段；没有就返回空列表"
    )
    confidence: float = Field(description="你对本次分析结果的把握，0 到 1 之间的小数")


class SpanLocated(BaseModel):
    """加上偏移量之后的片段，落库用。"""

    quote: str
    start: int
    end: int
