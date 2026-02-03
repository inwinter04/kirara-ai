from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Optional, List, Any
from datetime import datetime
import uuid
from sqlalchemy import Column, String, Integer, DateTime, Text
from kirara_ai.database import Base


# ===== 热度功能配置 =====
class HuluxiaHeatSchedule(BaseModel):
    """单次热度任务配置"""

    model_config = ConfigDict(
        extra="allow", json_schema_extra={"title": "热度任务计划"}
    )

    time: str = Field(
        title="执行时间",
        description="执行时间，格式 HH:MM，如 08:00",
        pattern=r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$",
        json_schema_extra={
            "placeholder": "08:00",
            "examples": ["08:00", "12:00", "20:00"],
        },
    )
    cat_ids: List[int] = Field(
        title="板块ID列表",
        description="要增加热度的板块ID列表，如 [92, 96]",
    )
    request_count: int = Field(
        title="请求次数",
        description="每个板块的请求次数",
        ge=1,
        le=10000,
        default=50,
        json_schema_extra={"examples": [50, 100]},
    )

    @field_validator("time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        """验证时间格式"""
        if not v:
            raise ValueError("时间不能为空")
        v = v.strip()
        try:
            hour, minute = map(int, v.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("时间格式错误，小时应在0-23之间，分钟应在0-59之间")
            return f"{hour:02d}:{minute:02d}"
        except Exception:
            raise ValueError("时间格式错误，应为 HH:MM 格式，如 08:00")

    def __repr__(self):
        return f"<HuluxiaHeatSchedule time={self.time}, cat_ids={self.cat_ids}, count={self.request_count}>"


class HuluxiaHeatConfig(BaseModel):
    """热度功能配置"""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "title": "热度功能",
            "description": "定时增加葫芦侠板块热度的功能配置",
        },
    )

    enable: bool = Field(
        title="启用热度功能",
        description="是否启用定时热度功能（当板块ID为0时该功能自动禁用）",
        default=False,
        json_schema_extra={"widget": "switch"},
    )
    schedules: List[HuluxiaHeatSchedule] = Field(
        title="执行计划",
        description="热度任务执行时间表，可以配置多个时间点",
        default=[],
        json_schema_extra={
            "widget": "array",
            "item_title": "计划",
            "examples": [[{"time": "08:00", "cat_ids": [92, 96], "request_count": 50}]],
        },
    )
    delay_min_ms: int = Field(
        title="最小延迟（毫秒）",
        description="两次请求之间的最小延迟时间（毫秒）",
        ge=50,
        le=5000,
        default=200,
        json_schema_extra={"examples": [200]},
    )
    delay_max_ms: int = Field(
        title="最大延迟（毫秒）",
        description="两次请求之间的最大延迟时间（毫秒）",
        ge=50,
        le=5000,
        default=300,
        json_schema_extra={"examples": [300]},
    )

    def __repr__(self):
        return (
            f"<HuluxiaHeatConfig enable={self.enable}, schedules={len(self.schedules)}>"
        )


# ===== 葫芦侠适配器配置 =====


class HuluxiaConfig(BaseModel):
    """
    葫芦侠适配器配置
    """

    model_config = ConfigDict(extra="allow")

    # ===== 基础配置 =====
    account: str = Field(title="账号", description="葫芦侠账号")

    password: str = Field(
        title="密码", description="葫芦侠密码", json_schema_extra={"widget": "password"}
    )

    # ===== 设备配置（内部使用，不暴露给用户）=====
    _device_code: Optional[str] = None

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = super().model_json_schema(*args, **kwargs)
        if "properties" in schema and "_device_code" in schema["properties"]:
            del schema["properties"]["_device_code"]
        return schema

    # ===== 平台配置 =====
    market_id: str = Field(
        default="tool_web",
        title="市场标识",
        description="tool_web（主站）或 floor_web（三楼）",
    )

    # ===== API配置（不可更改）=====
    @property
    def base_url(self) -> str:
        """API 基础 URL（固定值，不可更改）"""
        return "http://floor.huluxia.com"

    timeout: int = Field(
        title="请求超时（秒）", description="HTTP 请求超时时间", default=10, ge=5, le=60
    )

    # ===== 轮询配置 =====
    poll_interval: int = Field(
        title="轮询间隔（秒）",
        description="检查新消息的时间间隔，建议 5-10 秒",
        default=5,
        ge=2,
        le=300,
    )

    key_check_time: str = Field(
        title="每日检查登录有效期",
        description="每天定时检查登录有效性的时间，格式为 HH:MM，如 03:00",
        default="03:00",
        json_schema_extra={"widget": "text"},
    )

    # ===== 消息发送配置 =====
    comment_delay_ms: int = Field(
        title="评论发送延迟（毫秒）",
        description="两次评论之间的最小时间间隔，建议 500ms",
        default=500,
        ge=0,
        le=10000,
    )

    sensitive_words: List[str] = Field(
        title="敏感词列表",
        description="需要过滤的敏感词，每个词会被空格分隔",
        default=["测试", "演示", "示例", "test", "demo", "example"],
        json_schema_extra={"widget": "textarea"},
    )

    # ===== 热度功能配置 =====
    heat_enable: bool = Field(
        title="启用热度功能",
        description="是否启用定时热度功能（当板块ID为0时该功能自动禁用）",
        default=False,
        json_schema_extra={"widget": "switch"},
    )

    heat_board_ids: str = Field(
        title="指定板块ID",
        description="要增加热度的板块ID，多个用逗号分隔（如：92,96）",
        default="92,96",
        json_schema_extra={"placeholder": "92,96"},
    )

    heat_time: str = Field(
        title="执行时间",
        description="每天执行热度任务的时间（格式：HH:MM，如：08:00）",
        default="05:00",
        json_schema_extra={"placeholder": "08:00"},
    )

    heat_count: int = Field(
        title="每个板块请求次数",
        description="每个板块发送的请求次数",
        ge=1,
        le=10000,
        default=2500,
    )

    heat_delay_min_ms: int = Field(
        title="最小延迟（毫秒）",
        description="两次请求之间的最小延迟时间",
        ge=50,
        le=5000,
        default=300,
    )

    heat_delay_max_ms: int = Field(
        title="最大延迟（毫秒）",
        description="两次请求之间的最大延迟时间",
        ge=50,
        le=5000,
        default=500,
    )

    def __init__(self, **data):
        device_code_from_data = data.pop("device_code", None)
        super().__init__(**data)
        if device_code_from_data:
            object.__setattr__(self, "_device_code", device_code_from_data)
        if not self._device_code or len(self._device_code.strip()) == 0:
            object.__setattr__(self, "_device_code", str(uuid.uuid4()))
        object.__setattr__(self, "_cached_heat_config", None)

    @property
    def device_code(self) -> str:
        return self._device_code

    @property
    def heat(self) -> HuluxiaHeatConfig:
        """从扁平化字段构造HuluxiaHeatConfig对象（带缓存）"""
        if self._cached_heat_config is not None:
            return self._cached_heat_config

        # 如果没有启用，返回空配置
        if not self.heat_enable:
            config = HuluxiaHeatConfig(
                enable=False,
                schedules=[],
                delay_min_ms=self.heat_delay_min_ms,
                delay_max_ms=self.heat_delay_max_ms,
            )
            object.__setattr__(self, "_cached_heat_config", config)
            return config

        # 解析板块ID字符串
        cat_ids = []
        if self.heat_board_ids and self.heat_board_ids.strip():
            try:
                cat_ids = [
                    int(x.strip()) for x in self.heat_board_ids.split(",") if x.strip()
                ]
            except ValueError:
                pass

        # 如果没有有效的板块ID，返回空配置
        if not cat_ids:
            config = HuluxiaHeatConfig(
                enable=False,
                schedules=[],
                delay_min_ms=self.heat_delay_min_ms,
                delay_max_ms=self.heat_delay_max_ms,
            )
            object.__setattr__(self, "_cached_heat_config", config)
            return config

        # 构造schedule
        schedule = HuluxiaHeatSchedule(
            time=self.heat_time, cat_ids=cat_ids, request_count=self.heat_count
        )

        config = HuluxiaHeatConfig(
            enable=self.heat_enable,
            schedules=[schedule],
            delay_min_ms=self.heat_delay_min_ms,
            delay_max_ms=self.heat_delay_max_ms,
        )
        object.__setattr__(self, "_cached_heat_config", config)
        return config

    def model_dump(self, **kwargs):
        data = super().model_dump(**kwargs)
        if "exclude" not in kwargs or "device_code" not in kwargs.get("exclude", {}):
            data["device_code"] = self._device_code
        return data

    def model_dump_json(self, **kwargs):
        import json

        return json.dumps(self.model_dump(**kwargs))

    @field_validator("account")
    @classmethod
    def validate_account(cls, v):
        """验证账号格式"""
        if not v or len(v.strip()) == 0:
            raise ValueError("账号不能为空")
        return v.strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        """验证密码格式"""
        if not v or len(v.strip()) == 0:
            raise ValueError("密码不能为空")
        return v

    @field_validator("market_id")
    @classmethod
    def validate_market_id(cls, v):
        """验证市场标识"""
        if not v or len(v.strip()) == 0:
            raise ValueError("市场标识不能为空")
        v = v.strip()
        if v not in ["tool_web", "floor_web"]:
            raise ValueError("market_id 必须是 'tool_web' 或 'floor_web'")
        return v

    def __repr__(self):
        return f"<HuluxiaConfig account={self.account}, market_id={self.market_id}>"


class HuluxiaUserInfo(BaseModel):
    """
    葫芦侠用户信息
    """

    model_config = ConfigDict(extra="allow")

    user_id: int = Field(description="用户ID", alias="userID")
    role: int = Field(description="用户角色")
    nickname: str = Field(description="用户昵称", alias="nick")
    avatar: str = Field(description="用户头像URL")
    birthday: Optional[int] = Field(default=None, description="生日时间戳")
    age: Optional[int] = Field(default=None, description="年龄")
    gender: Optional[int] = Field(default=None, description="性别")
    level: Optional[int] = Field(default=None, description="用户等级")
    identity_title: Optional[str] = Field(
        default=None, description="用户称号", alias="identityTitle"
    )
    identity_color: Optional[int] = Field(
        default=None, description="称号背景色", alias="identityColor"
    )
    need_set_password: int = Field(
        description="是否需要设置密码", alias="needSetPassword"
    )
    need_set_user_info: int = Field(
        description="是否需要设置用户信息", alias="needSetUserInfo"
    )

    def __repr__(self):
        return f"<HuluxiaUserInfo user_id={self.user_id}, nickname={self.nickname}>"


class HuluxiaAdapterState(Base):
    """
    葫芦侠适配器状态持久化模型
    """

    __tablename__ = "huluxia_adapter_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    adapter_name = Column(String(100), unique=True, nullable=False, index=True)

    # 登录凭证（加密存储）
    user_id = Column(String(50))
    key = Column(Text)

    # 最后消息时间
    last_message_time = Column(DateTime)

    # 已处理的消息ID列表（JSON格式存储）
    processed_message_ids = Column(Text)

    # 凭证过期时间
    key_expires_at = Column(DateTime)

    # 创建时间和更新时间
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def get_processed_ids(self) -> List[str]:
        """
        获取已处理的消息ID列表

        Returns:
            消息ID列表
        """
        import json

        if not self.processed_message_ids:
            return []
        try:
            return json.loads(self.processed_message_ids)
        except Exception:
            return []

    def add_processed_id(self, message_id: str):
        """
        添加已处理的消息ID

        Args:
            message_id: 消息ID
        """
        processed_ids = self.get_processed_ids()
        if message_id not in processed_ids:
            processed_ids.append(message_id)
            import json

            self.processed_message_ids = json.dumps(processed_ids)

    def is_processed(self, message_id: str) -> bool:
        """
        检查消息ID是否已处理

        Args:
            message_id: 消息ID

        Returns:
            是否已处理
        """
        return message_id in self.get_processed_ids()

    def __repr__(self):
        return f"<HuluxiaAdapterState adapter_name={self.adapter_name}, user_id={self.user_id}>"


class HuluxiaMessageUser(BaseModel):
    """葫芦侠消息中的用户信息"""

    model_config = ConfigDict(extra="allow")

    userID: int = Field(description="用户ID", alias="userID")
    nick: str = Field(description="用户昵称")
    avatar: str = Field(description="用户头像URL")
    gender: int = Field(description="性别")
    age: int = Field(description="年龄")
    role: int = Field(description="用户角色")
    experience: int = Field(description="经验值")
    credits: int = Field(description="用户拥有的葫芦（葫芦侠内的货币）")
    level: int = Field(description="用户等级")
    integral: int = Field(description="用户经验（用于区分等级）")


class HuluxiaMessageContent(BaseModel):
    """葫芦侠消息内容"""

    model_config = ConfigDict(extra="allow")

    commentID: int = Field(description="评论ID")
    createTime: int = Field(description="消息创建时间戳（毫秒）")
    text: str = Field(description="消息文本内容")
    images: list = Field(description="图片列表")
    voice: str = Field(description="语音")
    voiceTime: int = Field(description="语音时长")
    score: int = Field(description="其他用户在消息内赠送的葫芦数量")
    scoreTxt: str = Field(description="赠送葫芦的留言")
    seq: int = Field(description="序号")
    state: int = Field(description="状态")
    isTop: int = Field(description="是否置顶")
    user: HuluxiaMessageUser = Field(description="发送用户的信息")


class HuluxiaMessageItem(BaseModel):
    """葫芦侠消息项"""

    model_config = ConfigDict(extra="allow")

    contentType: int = Field(description="内容类型")
    content: HuluxiaMessageContent = Field(description="消息内容")


class HuluxiaMessageListResponse(BaseModel):
    """葫芦侠消息列表响应"""

    model_config = ConfigDict(extra="allow")

    status: int = Field(description="响应状态，1表示成功")
    msg: str = Field(description="响应消息，成功时为空")
    datas: List[HuluxiaMessageItem] = Field(description="消息列表数组")
