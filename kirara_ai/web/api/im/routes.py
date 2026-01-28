import asyncio

from quart import Blueprint, g, jsonify, request

from kirara_ai.config.config_loader import CONFIG_FILE, ConfigJsonSchema, ConfigLoader
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.im.adapter import BotProfileAdapter
from kirara_ai.im.im_registry import IMRegistry
from kirara_ai.im.manager import IMManager
from kirara_ai.logger import get_logger

from ...auth.middleware import require_auth
from .models import (
    IMAdapterConfig,
    IMAdapterConfigSchema,
    IMAdapterList,
    IMAdapterResponse,
    IMAdapterStatus,
    IMAdapterTypes,
)

im_bp = Blueprint("im", __name__)

logger = get_logger("Web.IM")


def _create_adapter(manager: IMManager, name: str, adapter: str, config: dict):
    registry: IMRegistry = g.container.resolve(IMRegistry)
    adapter_info = registry.get_all_adapters()[adapter]
    adapter_class = adapter_info.adapter_class
    adapter_config_class = adapter_info.config_class
    adapter_config = adapter_config_class(**config)
    manager.create_adapter(name, adapter_class, adapter_config)


@im_bp.route("/types", methods=["GET"])
@require_auth
async def get_adapter_types():
    """获取所有可用的适配器类型"""
    registry: IMRegistry = g.container.resolve(IMRegistry)
    adapters = registry.get_all_adapters()
    types = [info.name for info in adapters.values()]
    return IMAdapterTypes(types=types, adapters=adapters).model_dump()


@im_bp.route("/adapters", methods=["GET"])
@require_auth
async def list_adapters():
    """获取所有已配置的适配器"""
    config = g.container.resolve(GlobalConfig)
    manager = g.container.resolve(IMManager)
    adapters = []
    for im in config.ims:
        is_running = manager.is_adapter_running(im.name)
        configs = im.config
        adapters.append(
            IMAdapterStatus(
                name=im.name, adapter=im.adapter, is_running=is_running, config=configs
            )
        )

    return IMAdapterList(adapters=adapters).model_dump()


@im_bp.route("/adapters/<adapter_id>", methods=["GET"])
@require_auth
async def get_adapter(adapter_id: str):
    """获取特定适配器的信息"""
    manager: IMManager = g.container.resolve(IMManager)

    # 查找适配器类型
    if not manager.has_adapter(adapter_id):
        return jsonify({"error": "Adapter not found"}), 404

    adapter_config = manager.get_adapter_config(adapter_id)
    adapter = manager.get_adapter(adapter_id)
    bot_profile = None
    if manager.is_adapter_running(adapter_id) and isinstance(
        adapter, BotProfileAdapter
    ):
        bot_profile = await adapter.get_bot_profile()

    return IMAdapterResponse(
        adapter=IMAdapterStatus(
            name=adapter_id,
            adapter=adapter_config.adapter,
            is_running=manager.is_adapter_running(adapter_id),
            config=adapter_config.config,
            bot_profile=bot_profile,
        )
    ).model_dump()


@im_bp.route("/adapters", methods=["POST"])
@require_auth
async def create_adapter():
    """创建新的适配器"""
    data = await request.get_json()
    adapter_info = IMAdapterConfig(**data)

    config: GlobalConfig = g.container.resolve(GlobalConfig)
    registry: IMRegistry = g.container.resolve(IMRegistry)
    manager: IMManager = g.container.resolve(IMManager)

    # 检查适配器类型是否存在
    if adapter_info.adapter not in registry.get_all_adapters():
        return jsonify({"error": "Invalid adapter type"}), 400

    # 检查ID是否已存在
    if manager.has_adapter(adapter_info.name):
        return jsonify({"error": "Adapter ID already exists"}), 400

    # 更新配置
    _create_adapter(
        manager, adapter_info.name, adapter_info.adapter, adapter_info.config
    )
    if adapter_info.enable:
        try:
            await manager.start_adapter(adapter_info.name, asyncio.get_event_loop())
        except Exception as e:
            manager.delete_adapter(adapter_info.name)
            return jsonify({"error": str(e)}), 500

    config.ims.append(adapter_info)
    # 保存配置到文件
    ConfigLoader.save_config_with_backup(CONFIG_FILE, config)

    return IMAdapterResponse(
        adapter=IMAdapterStatus(
            name=adapter_info.name,
            adapter=adapter_info.adapter,
            is_running=False,
            config=adapter_info.config,
        )
    ).model_dump()


@im_bp.route("/adapters/<adapter_id>", methods=["PUT"])
@require_auth
async def update_adapter(adapter_id: str):
    """更新适配器配置 (支持重命名)"""
    data = await request.get_json()
    try:
        adapter_info = IMAdapterConfig(**data)
    except Exception as e:
        return jsonify({"error": f"Invalid request data: {e}"}), 400

    config: GlobalConfig = g.container.resolve(GlobalConfig)
    manager: IMManager = g.container.resolve(IMManager)
    registry: IMRegistry = g.container.resolve(IMRegistry)
    loop = asyncio.get_event_loop()

    # 1. 检查原始适配器是否存在
    if not manager.has_adapter(adapter_id):
        return jsonify({"error": "Adapter not found"}), 404

    # 2. 如果名称改变，检查新名称是否冲突
    if adapter_id != adapter_info.name and manager.has_adapter(adapter_info.name):
        return jsonify(
            {"error": f"Adapter name '{adapter_info.name}' already exists"}
        ), 400

    # 3. 检查适配器类型是否有效
    if adapter_info.adapter not in registry.get_all_adapters():
        return jsonify({"error": "Invalid adapter type specified"}), 400

    # --- 停止旧适配器 (如果正在运行) ---
    if manager.is_adapter_running(adapter_id):
        try:
            await manager.stop_adapter(adapter_id, loop)
        except Exception as e:
            logger.error(f"Error stopping adapter {adapter_id}: {e}")

    # --- 更新 IMManager ---
    # 从管理器中删除旧的实例
    old_adapter_config = manager.get_adapter_config(adapter_id)
    manager.delete_adapter(adapter_id)

    # 保留旧配置中的 device_code（如果新配置中没有）
    if (
        "device_code" not in adapter_info.config
        and "device_code" in old_adapter_config.config
    ):
        adapter_info.config["device_code"] = old_adapter_config.config["device_code"]

    # 使用新名称和配置创建新的实例
    _create_adapter(
        manager, adapter_info.name, adapter_info.adapter, adapter_info.config
    )
    config.ims.append(adapter_info)

    # --- 尝试启动新适配器 (如果启用) ---
    is_now_running = False
    if adapter_info.enable:
        try:
            await manager.start_adapter(adapter_info.name, loop)
            is_now_running = True
        except Exception as e:
            logger.error(
                f"Failed to start adapter '{adapter_info.name}' after update: {e}"
            )

    # --- 保存配置到文件 ---
    # 无论是否启动成功，都保存更新后的配置
    ConfigLoader.save_config_with_backup(CONFIG_FILE, config)

    # --- 准备并返回响应 ---
    bot_profile = None
    if is_now_running:  # 仅在成功启动后尝试获取 profile
        adapter_instance = manager.get_adapter(adapter_info.name)
        if isinstance(adapter_instance, BotProfileAdapter):
            try:
                # 添加超时以防卡住
                bot_profile = await asyncio.wait_for(
                    adapter_instance.get_bot_profile(), timeout=5.0
                )
            except Exception as e:
                logger.error(
                    f"Failed to get bot profile for {adapter_info.name} after update: {e}"
                )

    return IMAdapterResponse(
        adapter=IMAdapterStatus(
            name=adapter_info.name,  # 使用新名称
            adapter=adapter_info.adapter,
            is_running=is_now_running,  # 反映当前实际运行状态
            config=adapter_info.config,
            bot_profile=bot_profile,
        )
    ).model_dump()


@im_bp.route("/adapters/<adapter_id>", methods=["DELETE"])
@require_auth
async def delete_adapter(adapter_id: str):
    """删除适配器"""
    config: GlobalConfig = g.container.resolve(GlobalConfig)
    manager: IMManager = g.container.resolve(IMManager)
    loop = asyncio.get_event_loop()

    # 先停止适配器
    if manager.is_adapter_running(adapter_id):
        await manager.stop_adapter(adapter_id, loop)

    # 从配置中删除
    manager.delete_adapter(adapter_id)

    # 保存配置到文件
    ConfigLoader.save_config_with_backup(CONFIG_FILE, config)

    return jsonify({"message": "Adapter deleted successfully"})


@im_bp.route("/adapters/<adapter_id>/start", methods=["POST"])
@require_auth
async def start_adapter(adapter_id: str):
    """启动适配器"""
    manager: IMManager = g.container.resolve(IMManager)
    loop = asyncio.get_event_loop()

    if manager.is_adapter_running(adapter_id):
        return jsonify({"error": "Adapter is already running"}), 400

    try:
        await manager.start_adapter(adapter_id, loop)
        return jsonify({"message": "Adapter started successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@im_bp.route("/adapters/<adapter_id>/stop", methods=["POST"])
@require_auth
async def stop_adapter(adapter_id: str):
    """停止适配器"""
    manager: IMManager = g.container.resolve(IMManager)
    loop = asyncio.get_event_loop()

    if not manager.is_adapter_running(adapter_id):
        return jsonify({"error": "Adapter is not running"}), 400

    try:
        await manager.stop_adapter(adapter_id, loop)
        return jsonify({"message": "Adapter stopped successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@im_bp.route("/types/<adapter_type>/config-schema", methods=["GET"])
@require_auth
async def get_adapter_config_schema(adapter_type: str):
    """获取指定适配器类型的配置字段模式"""
    try:
        registry: IMRegistry = g.container.resolve(IMRegistry)
        try:
            config_class = registry.get_config_class(adapter_type)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404

        schema = config_class.model_json_schema(schema_generator=ConfigJsonSchema)
        return IMAdapterConfigSchema(configSchema=schema).model_dump()
    except Exception as e:
        return IMAdapterConfigSchema(error=str(e)).model_dump()
