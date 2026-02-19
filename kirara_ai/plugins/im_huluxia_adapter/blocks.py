from typing import Any, Dict, Optional

from kirara_ai.im.adapter import IMAdapter
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.block import Block
from kirara_ai.workflow.core.block.input_output import Output


class GetHuluxiaCredentials(Block):
    """获取葫芦侠登录凭证，包括key和设备码"""

    name = "get_huluxia_credentials"
    container: DependencyContainer
    outputs = {
        "key": Output("key", "葫芦侠Key", str, "当前机器人的登录凭证Key"),
        "device_code": Output("device_code", "设备码", str, "当前机器人的设备码"),
    }

    def execute(self, **kwargs) -> Dict[str, Any]:
        adapter = self.container.resolve(IMAdapter)

        key = getattr(adapter, "key", None)
        if key is None:
            return {"key": None, "device_code": None}
        device_code = (
            adapter.config.device_code
            if hasattr(adapter.config, "device_code")
            else None
        )

        return {"key": key, "device_code": device_code}
