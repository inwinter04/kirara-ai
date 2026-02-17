import re
from datetime import datetime
from typing import Annotated, Any, Dict, List

from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.block import Block, Output, ParamMeta
from kirara_ai.workflow.core.block.input_output import Input


class TextBlock(Block):
    name = "text_block"
    outputs = {"text": Output("text", "文本", str, "文本")}

    def __init__(
        self, text: Annotated[str, ParamMeta(label="文本", description="要输出的文本")]
    ):
        self.text = text

    def execute(self) -> Dict[str, Any]:
        return {"text": self.text}


# 拼接文本
class TextConcatBlock(Block):
    name = "text_concat_block"
    inputs = {
        "text1": Input("text1", "文本1", str, "文本1"),
        "text2": Input("text2", "文本2", str, "文本2"),
    }
    outputs = {"text": Output("text", "拼接后的文本", str, "拼接后的文本")}

    def execute(self, text1: str, text2: str) -> Dict[str, Any]:
        return {"text": text1 + text2}


# 替换输入文本中的某一块文字为变量
class TextReplaceBlock(Block):
    name = "text_replace_block"
    inputs = {
        "text": Input("text", "原始文本", str, "原始文本"),
        "new_text": Input("new_text", "新文本", Any, "新文本"),  # type: ignore
    }
    outputs = {"text": Output("text", "替换后的文本", str, "替换后的文本")}

    def __init__(
        self,
        variable: Annotated[
            str, ParamMeta(label="被替换的文本", description="被替换的文本")
        ],
    ):
        self.variable = variable

    def execute(self, text: str, new_text: Any) -> Dict[str, Any]:
        return {"text": text.replace(self.variable, str(new_text))}


# 正则表达式提取
class TextExtractByRegexBlock(Block):
    name = "text_extract_by_regex_block"
    inputs = {"text": Input("text", "原始文本", str, "原始文本")}
    outputs = {"text": Output("text", "提取后的文本", str, "提取后的文本")}

    def __init__(
        self,
        regex: Annotated[str, ParamMeta(label="正则表达式", description="正则表达式")],
    ):
        self.regex = regex

    def execute(self, text: str) -> Dict[str, Any]:
        # 使用正则表达式提取文本
        regex = re.compile(self.regex)
        match = regex.search(text)
        # 如果匹配到，则返回匹配到的文本，否则返回空字符串
        if match and len(match.groups()) > 0:
            return {"text": match.group(1)}
        else:
            return {"text": ""}


# 获取当前时间
class CurrentTimeBlock(Block):
    name = "current_time_block"
    outputs = {"time": Output("time", "当前时间", str, "当前时间")}

    def execute(self) -> Dict[str, Any]:
        return {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


class CodeBlock(Block):
    name = "code_block"
    inputs = {}
    outputs = {}

    def __init__(
        self,
        inputs: Annotated[
            List[Dict[str, str]], ParamMeta(label="输入参数", description="输入参数")
        ],
        outputs: Annotated[
            List[Dict[str, str]], ParamMeta(label="输出参数", description="输出参数")
        ],
        code: Annotated[str, ParamMeta(label="代码", description="代码")],
    ):
        # 初始化实例的 inputs 和 outputs
        self.inputs = {}
        self.outputs = {}
        for input_spec in inputs:
            self.inputs[input_spec["name"]] = Input(
                input_spec["name"], input_spec["label"], Any, "user-specified object"
            )  # type: ignore
        for output_spec in outputs:
            self.outputs[output_spec["name"]] = Output(
                output_spec["name"], output_spec["label"], Any, "user-specified object"
            )  # type: ignore
        self.code = code

    def execute(self, **kwargs: Any) -> Dict[str, Any]:  # 使用 Any 兼容各种输入类型
        logger = get_logger("Block.Code")

        exec_globals = globals().copy()
        exec_locals: Dict[str, Any] = {}

        logger.debug(f"Executing code definition:\n{self.code}")
        try:
            exec(self.code, exec_globals, exec_locals)
        except Exception as e:
            logger.error(f"Error during code definition execution: {e}", exc_info=True)
            raise RuntimeError(f"Error in provided code definition: {e}") from e

        if "execute" not in exec_locals or not callable(exec_locals["execute"]):
            raise ValueError(
                "Provided code must define a callable function named 'execute'"
            )

        exec_locals["__input_kwargs__"] = kwargs
        exec_globals.update(exec_locals)
        call_code = "__result__ = execute(**__input_kwargs__)"

        logger.debug(f"Executing function call: execute(**{list(kwargs.keys())})")
        try:
            exec(call_code, exec_globals, exec_locals)
        except Exception as e:
            logger.error(
                f"Error during user function 'execute' execution: {e}", exc_info=True
            )
            raise RuntimeError(
                f"Error during execution of user function 'execute': {e}"
            ) from e

        if "__result__" not in exec_locals:
            # 如果 exec(call_code) 成功但没有 __result__，说明有内部问题
            logger.error(
                "Internal error: Result '__result__' not found after executing user code call."
            )
            raise RuntimeError("Failed to retrieve result from user code execution.")

        result = exec_locals["__result__"]

        return result
