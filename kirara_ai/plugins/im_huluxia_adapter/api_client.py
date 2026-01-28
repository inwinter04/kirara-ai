import aiohttp
import hashlib
from typing import Dict, Any, Optional
from kirara_ai.logger import get_logger
from .models import HuluxiaUserInfo

logger = get_logger("HuluxiaApiClient")


class HuluxiaApiClient:
    """
    葫芦侠 API 客户端
    封装所有与葫芦侠平台的 HTTP 交互
    """

    def __init__(self, base_url: str, device_code: str, timeout: int = 10):
        self.base_url = base_url

        # 只做校验，不生成（应由 Config 层保证）
        if not device_code:
            raise ValueError("device_code 不能为空，应由 Config 验证器保证")
        self.device_code = device_code

        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.session: Optional[aiohttp.ClientSession] = None

        # 登录后获取的凭证
        self.user_id: Optional[str] = None
        self._key: Optional[str] = None

    async def open(self):
        """
        打开 session（显式方法，推荐使用）
        """
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)

    async def close(self):
        """
        关闭 session（显式方法，推荐使用）
        """
        if self.session:
            await self.session.close()
            self.session = None

    async def __aenter__(self):
        """异步上下文管理器入口（向后兼容）"""
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口（向后兼容）"""
        await self.close()

    def _encrypt_password(self, password: str) -> str:
        """
        对用户密码进行MD5加密

        Args:
            password: 原始密码字符串

        Returns:
            MD5加密后的密码
        """
        return hashlib.md5(password.encode("utf-8")).hexdigest()

    def _generate_login_signature(self, username: str, md5_password: str) -> str:
        """
        生成登录请求的签名

        Args:
            username: 用户名
            md5_password: MD5加密后的密码

        Returns:
            MD5签名字符串
        """
        # 构造签名字符串
        sign_string = (
            "account"
            + username
            + "device_code[d]"
            + self.device_code
            + "password"
            + md5_password
            + "voice_codefa1c28a5b62e79c3e63d9030b6142e4b"
        )

        # 返回MD5加密后的签名
        return hashlib.md5(sign_string.encode("utf-8")).hexdigest()

    async def login(
        self, account: str, password: str, login_main_site: bool = True
    ) -> Dict[str, Any]:
        """
        登录并获取访问令牌

        Args:
            account: 账号
            password: 密码
            login_main_site: True登录主站，False登录三楼

        Returns:
            包含用户信息和 key 的字典

        Raises:
            Exception: 登录失败时抛出
        """
        # 1. 加密密码
        md5_password = self._encrypt_password(password)

        # 2. 生成签名
        sign = self._generate_login_signature(account, md5_password)

        # 3. 构造URL参数
        market_id = "tool_web" if login_main_site else "floor_web"
        device_code_encoded = f"%5Bd%5D{self.device_code}"

        url = (
            f"{self.base_url}/account/login/ANDROID/4.1.8"
            f"?platform=2"
            f"&gkey=000000"
            f"&app_version=4.3.0.4"
            f"&versioncode=20141495"
            f"&market_id={market_id}"
            f"&_key="
            f"&device_code={device_code_encoded}"
            f"&phone_brand_type=UN"
        )

        # 4. 构造请求数据
        data = {
            "account": account,
            "login_type": "2",
            "password": md5_password,
            "sign": sign,
        }

        # 5. 设置请求头
        headers = {
            "User-Agent": "okhttp/3.8.1",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        try:
            # 6. 发送请求
            if not self.session:
                raise Exception("HTTP session 未初始化")

            async with self.session.post(url, data=data, headers=headers) as response:
                response_text = await response.text()

                try:
                    response_data = await response.json()
                except Exception as e:
                    logger.error(f"JSON解析错误: {e}, 响应内容: {response_text}")
                    raise Exception(f"JSON解析错误: {e}")

                # 7. 提取用户信息
                if "user" in response_data and "_key" in response_data:
                    user_data = response_data["user"]
                    self.user_id = user_data["userID"]
                    self._key = response_data["_key"]

                    # 使用 HuluxiaUserInfo 模型解析用户信息
                    user_info = HuluxiaUserInfo(**user_data)

                    logger.info(f"登录成功！用户ID: {self.user_id}")

                    return {
                        "user_id": self.user_id,
                        "key": self._key,
                        "user_info": user_info,
                    }
                else:
                    msg = response_data.get("msg", "未知错误")
                    logger.error(f"登录失败：{msg}")
                    raise Exception(f"登录失败：{msg}")

        except aiohttp.ClientError as e:
            logger.error(f"网络请求错误: {e}")
            raise Exception(f"网络请求错误: {e}")
        except Exception as e:
            logger.error(f"登录异常: {e}")
            raise

    def get_key(self) -> Optional[str]:
        """
        获取登录后的 key

        Returns:
            key 字符串，如果未登录则返回 None
        """
        return self._key

    def get_user_id(self) -> Optional[str]:
        """
        获取登录后的用户 ID

        Returns:
            用户 ID，如果未登录则返回 None
        """
        return self.user_id

    async def verify_login_status(self) -> bool:
        """
        验证登录状态是否有效

        Returns:
            True 表示有效，False 表示无效
        """
        if not self._key:
            return False

        url = f"{self.base_url}/account/security/info/ANDROID/4.2.2?_key={self._key}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        try:
            if not self.session:
                return False

            async with self.session.get(url, headers=headers) as response:
                response_data = await response.json()

                # 检查状态字段
                status = response_data.get("status", 0)
                return status == 1

        except Exception as e:
            logger.error(f"验证登录状态失败: {e}")
            return False
