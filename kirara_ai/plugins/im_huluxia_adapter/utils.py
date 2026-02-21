import re

SUPPORTED_EMOTICONS = {
    "哈哈",
    "吐舌",
    "啊",
    "酷",
    "怒",
    "开心",
    "汗",
    "泪",
    "黑线",
    "鄙视",
    "真棒",
    "钱",
    "疑问",
    "阴险",
    "咦",
    "委屈",
    "花心",
    "呼~",
    "笑眼",
    "冷",
    "太开心",
    "滑稽",
    "勉强",
    "狂汗",
    "乖",
    "睡觉",
    "惊哭",
    "升起",
    "惊讶",
    "喷",
    "爱心",
    "心碎",
    "玫瑰",
    "礼物",
    "彩虹",
    "星星月亮",
    "太阳",
    "铅笔",
    "灯泡",
    "茶杯",
    "蛋糕",
    "音乐",
    "haha",
    "胜利",
    "大拇指",
    "弱",
    "OK",
    "赖皮",
    "感动",
    "十分惊讶",
    "怒气",
    "哭泣",
    "吃惊",
    "嘲弄",
    "飘过",
    "转圈哭",
    "神经病",
    "揪耳朵",
    "惊汗",
    "隐身",
    "不要嘛",
    "遁",
    "不公平",
    "爬来了",
    "蛋花哭",
    "温柔",
    "点头",
    "撒钱",
    "献花",
    "寒",
}


def clean_unsupported_emoticons(text: str) -> str:
    """清理葫芦侠平台不支持的表情"""

    def replace_unsupported(match):
        emoticon_name = match.group(1)
        if emoticon_name not in SUPPORTED_EMOTICONS:
            return ""
        return match.group(0)

    return re.sub(r"\[([^\]]+)\]", replace_unsupported, text)


def clean_markdown(text: str) -> str:
    """
    清理Markdown语法，转换为纯文本

    使用严格的边界匹配，避免误处理非Markdown上下文中的符号
    （如数学表达式 1*2*3、SQL语句 SELECT * FROM 等）
    """

    cleaned = text

    # 1. 清理代码块（```code```）- 优先处理，避免内部内容被其他规则误处理
    cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)

    # 2. 清理标题（# ### 等）- 要求行首
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)

    # 3. 清理粗体（**text** 或 __text__）
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)

    # 4. 清理斜体（*text*）
    # 使用严格边界：星号前后必须是非空白非星号字符，避免匹配数学表达式
    cleaned = re.sub(r"(?<=[^\s*])\*(?!\*)(.+?)(?<!\*)\*(?=[^\s*])", r"\1", cleaned)

    # 5. 清理删除线（~~text~~）
    cleaned = re.sub(r"~~(.+?)~~", r"\1", cleaned)

    # 6. 清理行内代码（`code`）- 要求反引号内有内容
    cleaned = re.sub(r"`([^`\s][^`]*[^`\s])`", r"\1", cleaned)
    cleaned = re.sub(r"`([^`\s])`", r"\1", cleaned)

    # 7. 清理链接 [text](url)
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)

    # 8. 清理图片 ![alt](url)
    cleaned = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", r"\1", cleaned)

    # 9. 清理引用（> text）- 要求行首
    cleaned = re.sub(r"^>\s+", "", cleaned, flags=re.MULTILINE)

    # 10. 清理无序列表（- 或 *）- 要求行首
    cleaned = re.sub(r"^[\-\*]\s+", "", cleaned, flags=re.MULTILINE)

    # 11. 清理有序列表（1. 2. 等）- 要求行首
    cleaned = re.sub(r"^\d+\.\s+", "", cleaned, flags=re.MULTILINE)

    # 12. 清理水平线（--- 或 *** 或 ___）- 要求独立成行
    cleaned = re.sub(r"^[-\*_]{3,}$", "", cleaned, flags=re.MULTILINE)

    return cleaned.strip()
