from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Optional, List, Any
from datetime import datetime
import uuid
from sqlalchemy import Column, String, Integer, DateTime, Text
from kirara_ai.database import Base


class HuluxiaConfig(BaseModel):
    """
    葫芦侠适配器配置
    """

    model_config = ConfigDict(extra="allow")

    # ===== 基础配置 =====
    account: str = Field(title="账号", description="葫芦侠账号")

    password: str = Field(title="密码", description="葫芦侠密码")

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

    def __init__(self, **data):
        device_code_from_data = data.pop("device_code", None)
        super().__init__(**data)
        if device_code_from_data:
            object.__setattr__(self, "_device_code", device_code_from_data)
        if not self._device_code or len(self._device_code.strip()) == 0:
            object.__setattr__(self, "_device_code", str(uuid.uuid4()))

    @property
    def device_code(self) -> str:
        return self._device_code

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
