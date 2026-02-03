import aiohttp
import hashlib
import time
import random
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

    async def _safe_json_parse(self, response, response_text: str) -> Dict[str, Any]:
        """安全解析JSON响应"""
        try:
            return await response.json()
        except Exception as e:
            logger.error(f"JSON解析错误: {e}, 响应内容: {response_text}")
            raise Exception(f"JSON解析错误: {e}")

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
            # 5. 发送请求
            if not self.session:
                raise Exception("HTTP session 未初始化")

            logger.debug(f"[API_CLIENT] 登录请求URL: {url}")
            logger.debug(
                f"[API_CLIENT] 登录请求数据: account={account}, password={'*' * len(md5_password)}"
            )

            async with self.session.post(url, data=data, headers=headers) as response:
                response_text = await response.text()
                response_data = await self._safe_json_parse(response, response_text)

                logger.info(
                    f"[API_CLIENT] 登录响应: status={response.status}, response_data={response_data}"
                )

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
                logger.warning("[VERIFY] session 未初始化，返回 False")
                return False

            async with self.session.get(url, headers=headers) as response:
                response_data = await response.json()

                # 检查状态字段
                status = response_data.get("status", 0)
                msg = response_data.get("msg", "")

                logger.debug(f"[VERIFY] 解析结果: status={status}, msg={msg}")

                result = status == 1
                logger.info(
                    f"[VERIFY] 验证结果: {'有效' if result else '无效'} (status={status})"
                )

                return result

        except Exception as e:
            logger.error(f"[VERIFY] 验证登录状态失败: {e}")
            import traceback

            logger.error(f"[VERIFY] 异常堆栈: {traceback.format_exc()}")
            return False

    async def get_message_list(
        self, _key: str, market_id: str, start: int = 0, count: int = 20
    ) -> Dict[str, Any]:
        """
        获取最新消息列表

        Args:
            _key: 用户认证密钥
            market_id: 市场ID (tool_web 或 floor_web)
            start: 起始位置，默认0
            count: 获取数量，默认20

        Returns:
            API响应的字典

        Raises:
            Exception: 请求失败时抛出
        """
        # 1. 构造URL参数
        device_code_encoded = f"%5Bd%5D{self.device_code}"

        url = (
            f"{self.base_url}/message/new/list/ANDROID/4.1.8"
            f"?platform=2"
            f"&gkey=000000"
            f"&app_version=4.3.0.5.1"
            f"&versioncode=20141498"
            f"&market_id={market_id}"
            f"&_key={_key}"
            f"&device_code={device_code_encoded}"
            f"&phone_brand_type=UN"
            f"&type_id=8"
            f"&start={start}"
            f"&count={count}"
        )

        # 2. 设置请求头
        headers = {
            "User-Agent": "okhttp/3.8.1",
        }

        try:
            # 3. 发送GET请求
            if not self.session:
                raise Exception("HTTP session 未初始化")

            async with self.session.get(url, headers=headers) as response:
                response_text = await response.text()
                response_data = await self._safe_json_parse(response, response_text)

                return response_data

        except aiohttp.ClientError as e:
            logger.error(f"网络请求错误: {e}")
            raise Exception(f"网络请求错误: {e}")
        except Exception as e:
            logger.error(f"获取消息列表异常: {e}")
            raise

    def _generate_upload_sign(self, _key: str, nonce_str: str, timestamp: int) -> str:
        """
        生成图片上传请求签名

        Args:
            _key: 用户认证密钥
            nonce_str: 随机32位字符串
            timestamp: 时间戳（毫秒）

        Returns:
            MD5签名字符串（大写）
        """
        # 按照文档格式：参数名=值&参数名=值...&secret=密钥
        sign_string = (
            f"_key={_key}"
            f"&app_version=4.3.0.2"
            f"&device_code=[d]{self.device_code}"
            f"&gkey=000000"
            f"&market_id=floor_web"
            f"&nonce_str={nonce_str}"
            f"&platform=2"
            f"&timestamp={timestamp}"
            f"&use_type=2"
            f"&versioncode=20141492"
            f"&secret=my_sign@huluxia.com"
        )

        sign = hashlib.md5(sign_string.encode("utf-8")).hexdigest().upper()

        return sign

    async def upload_image(self, _key: str, image_data: bytes, filename: str) -> str:
        """
        上传图片到葫芦侠服务器

        Args:
            _key: 用户认证密钥
            image_data: 图片二进制数据
            filename: 文件名

        Returns:
            图片 fid

        Raises:
            Exception: 上传失败时抛出
        """
        # 1. 生成必要的参数
        timestamp = int(time.time() * 1000)
        nonce_str = self._generate_nonce_str()
        sign = self._generate_upload_sign(_key, nonce_str, timestamp)

        # 2. 构造URL参数
        device_code_encoded = f"%5Bd%5D{self.device_code}"
        url = (
            f"http://upload.huluxia.com/upload/v3/image"
            f"?platform=2"
            f"&gkey=000000"
            f"&app_version=4.3.0.2"
            f"&versioncode=20141492"
            f"&market_id=floor_web"
            f"&_key={_key}"
            f"&device_code={device_code_encoded}"
            f"&use_type=2"
            f"&sign={sign}"
            f"&timestamp={timestamp}"
            f"&nonce_str={nonce_str}"
        )

        # 3. 构造multipart/form-data请求
        headers = {
            "User-Agent": "okhttp/3.8.1",
        }

        # 4. 构造表单数据
        data = aiohttp.FormData()
        data.add_field(
            "file",
            image_data,
            filename=filename,
            content_type=self._guess_content_type(filename),
        )

        try:
            if not self.session:
                raise Exception("HTTP session 未初始化")

            async with self.session.post(url, data=data, headers=headers) as response:
                response_text = await response.text()
                response_data = await self._safe_json_parse(response, response_text)

                # 提取 fid
                if "fid" in response_data:
                    fid = response_data["fid"]
                    logger.info(f"[UPLOAD_IMAGE] 图片上传成功: fid={fid}")
                    return fid
                else:
                    msg = response_data.get("msg", "未知错误")
                    logger.error(f"[UPLOAD_IMAGE] 图片上传失败：{msg}")
                    raise Exception(f"图片上传失败：{msg}")

        except aiohttp.ClientError as e:
            logger.error(f"[UPLOAD_IMAGE] 网络请求错误: {e}")
            raise Exception(f"网络请求错误: {e}")
        except Exception as e:
            logger.error(f"[UPLOAD_IMAGE] 上传异常: {e}")
            raise

    def _generate_nonce_str(self) -> str:
        """生成随机32位字符串"""
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        return "".join(random.choice(chars) for _ in range(32))

    def _guess_content_type(self, filename: str) -> str:
        """根据文件名猜测Content-Type"""
        if "." not in filename:
            return "application/octet-stream"

        extension = filename.split(".")[-1].lower()
        content_types = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
            "bmp": "image/bmp",
        }
        return content_types.get(extension, "application/octet-stream")

    def _generate_comment_sign(
        self, _key: str, post_id: str, text: str, comment_id: int = 0, images: str = ""
    ) -> str:
        """
        生成评论请求签名

        Args:
            _key: 用户认证密钥
            post_id: 帖子ID
            text: 评论内容
            comment_id: 评论ID，新评论为0
            images: 图片 fid 列表（逗号分隔）

        Returns:
            MD5签名字符串（大写）
        """
        # 注意：根据文档，评论签名中 device_code 的值应该是空字符串
        params = {
            "_key": _key,
            "comment_id": str(comment_id),
            "device_code": "",  # 评论签名中 device_code 为空
            "images": images,
            "post_id": post_id,
            "text": text,
        }

        # 按 key 升序排序并拼接
        sorted_params = sorted(params.items())
        sign_string_without_key = "".join([f"{k}{v}" for k, v in sorted_params])

        # 追加密钥并计算 MD5
        sign_string = sign_string_without_key + "fa1c28a5b62e79c3e63d9030b6142e4b"
        sign = hashlib.md5(sign_string.encode("utf-8")).hexdigest().upper()

        return sign

    async def create_comment(
        self,
        _key: str,
        market_id: str,
        post_id: int,
        text: str,
        comment_id: int = 0,
        images: str = "",
    ) -> Dict[str, Any]:
        """
        创建评论（发送消息）

        Args:
            _key: 用户认证密钥
            market_id: 市场ID
            post_id: 帖子ID
            text: 评论内容
            comment_id: 评论ID，新评论为0
            images: 图片 fid 列表（逗号分隔）

        Returns:
            API响应的字典

        Raises:
            Exception: 请求失败时抛出
        """
        # 1. 生成签名
        sign = self._generate_comment_sign(_key, str(post_id), text, comment_id, images)

        # 2. 构造 URL 参数
        url = (
            f"{self.base_url}/comment/create/ANDROID/4.2"
            f"?platform=2"
            f"&gkey=000000"
            f"&app_version=4.3.0.2"
            f"&versioncode=20141492"
            f"&market_id={market_id}"
            f"&_key={_key}"
        )

        # 3. 构造请求体
        data = {
            "post_id": post_id,
            "comment_id": comment_id,
            "text": text,
            "patcha": "",
            "images": images,
            "remindUsers": "",
            "sign": sign,
        }

        # 4. 设置请求头
        headers = {
            "User-Agent": "okhttp/3.8.1",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        try:
            # 5. 发送 POST 请求
            if not self.session:
                raise Exception("HTTP session 未初始化")

            async with self.session.post(url, data=data, headers=headers) as response:
                response_text = await response.text()
                response_data = await self._safe_json_parse(response, response_text)

                return response_data

        except aiohttp.ClientError as e:
            logger.error(f"网络请求错误: {e}")
            raise Exception(f"网络请求错误: {e}")
        except Exception as e:
            logger.error(f"创建评论异常: {e}")
            raise
