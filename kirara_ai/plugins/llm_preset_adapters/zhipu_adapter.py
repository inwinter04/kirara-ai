from .openai_adapter import OpenAIAdapterChatBase, OpenAIConfig


class ZhipuAIConfig(OpenAIConfig):
    api_base: str = "https://open.bigmodel.cn/api/paas/v4"


class ZhipuAIAdapter(OpenAIAdapterChatBase):
    def __init__(self, config: ZhipuAIConfig):
        super().__init__(config)

    async def auto_detect_models(self):
        """智谱清言不支持自动检测模型列表"""
        return []
