# 葫芦侠适配器

## 简介

葫芦侠平台适配器，用于 Kirara AI 框架。

## 当前状态

✅ **已实现功能：**
- 基础配置管理
- 登录功能（支持自定义设备码）
- 插件注册
- 基础适配器框架

⏳ **待实现功能：**
- 消息接收和转换
- 消息发送
- 用户资料查询
- 消息监控和轮询机制

## 登录功能说明

### 配置参数

| 参数 | 说明 | 默认值 |
|-----|------|--------|
| `account` | 葫芦侠账号 | - |
| `password` | 葫芦侠密码 | - |
| `device_code` | 设备识别码 | `b305cc73-8db8-4a25-886f-e73c502b1e99` |
| `market_id` | 市场标识（tool_web 或 floor_web） | `tool_web` |
| `base_url` | API 基础地址 | `http://floor.huluxia.com` |
| `timeout` | 请求超时时间（秒） | `10` |

### 测试登录

使用测试脚本验证登录功能：

```bash
python kirara_ai/plugins/im_huluxia_adapter/test_login.py
```

修改 `test_login.py` 中的账号和密码后运行。

### 登录流程

1. 密码 MD5 加密
2. 生成请求签名
3. 发送登录请求
4. 提取用户 ID 和 key
5. 验证登录状态

## 配置示例

在 Kirara AI WebUI 中配置：

1. 账号：`your_account`
2. 密码：`your_password`
3. 设备码：`b305cc73-8db8-4a25-886f-e73c502b1e99`（可自定义）
4. 市场标识：`tool_web`
5. 轮询间隔：`30`

## 开发说明

### 目录结构

```
kirara_ai/plugins/im_huluxia_adapter/
├── __init__.py      # 插件入口
├── adapter.py       # 主适配器类
├── api_client.py    # API 客户端（实现登录）
├── models.py        # 配置类
├── setup.py         # 插件配置
├── test_login.py    # 登录测试脚本
└── assets/          # 资源文件
    └── huluxia.png  # 平台图标
```

### 下一步

需要提供以下 API 文档：
- 消息获取接口（获取用户的新消息）
- 消息发送接口
- 用户资料查询接口

## 注意事项

- 当前版本仅实现了登录功能
- 消息监控功能待完善
- 请确保轮询间隔不低于 30 秒，避免触发平台反爬机制

## 参考资料

- [登录接口文档](../../docs/葫芦侠接口文档/登录接口文档.md)
- [开发计划](../../docs/huluxia_adapter_plan.md)
- [开发指南](../../docs/huluxia_development_guide.md)
